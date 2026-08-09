"""Long-running sidecar client — ndjson JSON-RPC over stdio.

One process, spawned on first use, killed at interpreter exit. A dedicated
reader thread routes stdout lines: responses complete the pending request,
``progress`` notifications are forwarded to that request's ``on_progress``
callback (the exact ``analysis_component`` event dict, so the TUI checklist
consumes them unchanged). Requests are serialized — the engines make one call
at a time, and the protocol (contracts/v1/rpc.md) does not need pipelining.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from yeaboi.gocore.discovery import find_core_binary

logger = logging.getLogger(__name__)

CONTRACT_VERSION = 1

_HELLO_TIMEOUT = 10.0
_DEFAULT_TIMEOUT = 600.0


class CoreError(Exception):
    """Any sidecar failure — spawn, protocol, RPC error, timeout, crash.

    Callers treat every CoreError the same way: log one line and fall back to
    the Python implementation.
    """


def is_enabled() -> bool:
    """The ``YEABOI_GO`` opt-in flag (pilot phase: off by default)."""
    return os.environ.get("YEABOI_GO", "").strip().lower() in {"1", "true", "yes", "on"}


class CoreClient:
    """One sidecar process. Use :func:`get_client`, not this directly."""

    def __init__(self, binary: str) -> None:
        logger.info("gocore: spawning sidecar %s", binary)
        self._proc = subprocess.Popen(  # noqa: S603 — binary resolved by discovery, not user input
            [binary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._request_lock = threading.Lock()  # serializes whole request/response cycles
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_events: dict[int, threading.Event] = {}
        self._progress: dict[int, Callable[[dict], None]] = {}
        self._reader = threading.Thread(target=self._read_stdout, daemon=True, name="gocore-reader")
        self._reader.start()
        self._stderr_drain = threading.Thread(target=self._read_stderr, daemon=True, name="gocore-stderr")
        self._stderr_drain.start()
        atexit.register(self.close)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Terminate the sidecar. Idempotent; registered atexit."""
        proc = self._proc
        if proc.poll() is None:
            logger.info("gocore: terminating sidecar (pid %d)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    # ── RPC ───────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        on_progress: Callable[[dict], None] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """One JSON-RPC call. Returns the ``result`` object or raises CoreError."""
        with self._request_lock:
            if not self.alive:
                raise CoreError("sidecar process is not running")
            self._next_id += 1
            request_id = self._next_id
            done = threading.Event()
            self._pending_events[request_id] = done
            if on_progress is not None:
                self._progress[request_id] = on_progress
            try:
                line = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                with self._write_lock:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write(line + "\n")
                    self._proc.stdin.flush()
                if not done.wait(timeout):
                    raise CoreError(f"{method} timed out after {timeout:.0f}s")
                response = self._pending.pop(request_id)
            except BrokenPipeError as exc:
                raise CoreError(f"sidecar pipe broken during {method}") from exc
            finally:
                self._pending_events.pop(request_id, None)
                self._progress.pop(request_id, None)
                self._pending.pop(request_id, None)
            if "error" in response:
                error = response["error"] or {}
                raise CoreError(f"{method} failed: [{error.get('code')}] {error.get('message')}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise CoreError(f"{method} returned a malformed result")
            return result

    def hello(self) -> dict[str, Any]:
        """Handshake; raises CoreError on any mismatch."""
        result = self.request("core.hello", {}, timeout=_HELLO_TIMEOUT)
        version = result.get("contract_version")
        if version != CONTRACT_VERSION:
            raise CoreError(f"contract version mismatch: sidecar speaks {version!r}, client speaks {CONTRACT_VERSION}")
        logger.info("gocore: handshake ok — %s %s", result.get("name"), result.get("version"))
        return result

    # ── Reader threads ────────────────────────────────────────────────────

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                logger.warning("gocore: dropping non-JSON stdout line")
                continue
            if not isinstance(message, dict):
                continue
            if message.get("method") == "progress":
                params = message.get("params") or {}
                callback = self._progress.get(params.get("request_id"))
                event = params.get("event")
                if callback is not None and isinstance(event, dict):
                    try:
                        callback(event)
                    except Exception:  # noqa: BLE001 — a UI callback must not kill the reader
                        logger.exception("gocore: on_progress callback raised")
                continue
            message_id = message.get("id")
            if message_id in self._pending_events:
                self._pending[message_id] = message
                self._pending_events[message_id].set()
        # EOF: the sidecar exited — release every waiter so requests fail fast.
        for message_id, event in list(self._pending_events.items()):
            self._pending.setdefault(message_id, {"error": {"code": -1, "message": "sidecar exited"}})
            event.set()

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.rstrip()
            if line:
                logger.debug("gocore[stderr]: %s", line)


# ── Singleton ─────────────────────────────────────────────────────────────

_client_lock = threading.Lock()
_client: CoreClient | None = None
_client_failed = False


def get_client() -> CoreClient | None:
    """The process-wide sidecar client, or None when the Go path is unavailable.

    None when: the YEABOI_GO flag is off, no binary is found, or the spawn or
    handshake failed earlier in this process (one attempt per process — a
    broken binary must not add spawn latency to every engine run).
    """
    global _client, _client_failed
    if not is_enabled():
        return None
    with _client_lock:
        if _client is not None and _client.alive:
            return _client
        if _client_failed:
            return None
        binary = find_core_binary()
        if binary is None:
            logger.info("gocore: YEABOI_GO is on but no yeaboi-core binary found — using the Python path")
            _client_failed = True
            return None
        try:
            client = CoreClient(binary)
            client.hello()
        except Exception as exc:  # noqa: BLE001 — any failure means "no Go path"
            logger.warning("gocore: sidecar unavailable (%s: %s) — using the Python path", type(exc).__name__, exc)
            _client_failed = True
            return None
        _client = client
        return _client
