"""In-memory MCP dispatch — the desktop tool surface IS the MCP surface.

# See docs: "MCP Server" — result envelope and LLM modes

Rather than re-wrapping ~57 engines in bespoke HTTP handlers (a third mirror
of the tool layer, and the drift the parity registry exists to prevent), the
desktop backend hosts the real FastMCP app in-process and drives it through
the SDK's in-memory transport — the same path ``tests/unit/test_mcp_server.py``
uses. Envelope shape, ``run_engine``'s engine lock, warnings and ``llm_mode``
resolution are inherited rather than reimplemented. ``llm_mode`` resolves to
``provider``/``fallback`` here: an in-memory client advertises no sampling.

One background thread owns an asyncio loop for the lifetime of the process;
HTTP handler threads submit calls with ``run_coroutine_threadsafe`` and block
on the result. Progress notifications from ``ctx.report_progress`` are
republished on the event bus keyed by the caller's ``op_id``.

The ``mcp`` extra is optional. ``start()`` raises :class:`DispatcherUnavailableError`
when it is missing, and the server keeps running — tool routes answer 503 with
the install hint instead of taking the whole backend down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

logger = logging.getLogger(__name__)

MISSING_MCP_HINT = "the 'mcp' extra is not installed — install yeaboi[mcp] to serve tool calls"

_START_TIMEOUT_SECONDS = 30.0
#: Ceiling for one tool call end-to-end. Generous: a deep team analysis is
#: minutes of work. The client's own cancel path is the op_id, not this.
CALL_TIMEOUT_SECONDS = 60 * 60.0


class DispatcherUnavailableError(RuntimeError):
    """The dispatcher is not serving (missing extra or failed startup)."""


class McpDispatcher:
    """Owns the loop thread and the in-memory FastMCP client session."""

    def __init__(self, bus=None) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._client = None
        self._tools: frozenset[str] = frozenset()

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self, timeout: float = _START_TIMEOUT_SECONDS) -> None:
        """Spawn the loop thread and block until the tool session is live."""
        self._thread = threading.Thread(target=self._run, name="app-mcp-dispatch", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise DispatcherUnavailableError("MCP dispatcher did not start in time")
        if self._startup_error is not None:
            raise DispatcherUnavailableError(str(self._startup_error)) from self._startup_error
        logger.info("MCP dispatcher live: %d tools", len(self._tools))

    def stop(self) -> None:
        """Stop the loop thread; safe to call when never started or failed."""
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("MCP dispatcher stopped")

    @property
    def available(self) -> bool:
        return self._client is not None and self._startup_error is None

    def tool_names(self) -> frozenset[str]:
        return self._tools

    # ── calls ──────────────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict | None = None, *, op_id: str | None = None) -> dict:
        """Run one tool call and return its envelope dict.

        Raises :class:`DispatcherUnavailableError` when not serving and
        :class:`ValueError` for an unknown tool (the router maps that to 400;
        the tool route pre-checks and answers 404 with the inventory intact).
        """
        if not self.available or self._loop is None:
            raise DispatcherUnavailableError(MISSING_MCP_HINT if self._startup_error else "dispatcher not started")
        if name not in self._tools:
            raise ValueError(f"unknown tool: {name}")
        future = asyncio.run_coroutine_threadsafe(self._call(name, arguments or {}, op_id), self._loop)
        return future.result(timeout=CALL_TIMEOUT_SECONDS)

    async def _call(self, name: str, arguments: dict, op_id: str | None) -> dict:
        progress_callback = self._progress_publisher(name, op_id) if (self._bus and op_id) else None
        result = await self._client.call_tool(name, arguments, progress_callback=progress_callback)
        # FastMCP tools return one text block carrying the envelope JSON —
        # the same decode the MCP server tests use.
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                return json.loads(text)
        raise ValueError(f"tool {name} returned no decodable content")

    def _progress_publisher(self, name: str, op_id: str):
        async def _on_progress(progress: float, total: float | None, message: str | None) -> None:
            self._bus.publish("progress", op_id=op_id, tool=name, progress=progress, total=total, message=message)

        return _on_progress

    # ── loop thread ────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as exc:  # noqa: BLE001 - startup/teardown failures surface via start()
            self._startup_error = exc
            if not self._ready.is_set():
                logger.warning("MCP dispatcher failed to start: %s", exc)
            else:  # pragma: no cover - post-start crash; callers see DispatcherUnavailableError next call
                logger.error("MCP dispatcher crashed: %s", exc, exc_info=True)
            self._client = None
            self._ready.set()

    async def _main(self) -> None:
        # Lazy imports: the mcp extra is optional (same convention as
        # yeaboi.mcp.server), and a missing module becomes a 503, not a crash.
        from mcp.shared.memory import create_connected_server_and_client_session

        from yeaboi.mcp.server import create_app

        app = create_app()
        async with create_connected_server_and_client_session(app._mcp_server) as client:
            listed = await client.list_tools()
            self._tools = frozenset(tool.name for tool in listed.tools)
            self._client = client
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            self._ready.set()
            await self._stop.wait()
        self._client = None
