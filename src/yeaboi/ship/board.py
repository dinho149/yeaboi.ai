"""Live state for the ship board — one supervised run, projected for a browser.

The board is the retro/poker pattern pointed at a ship run: a single
lock-guarded object the HTTP handler reads through ``self.server``, with a
``revision()`` the long poll and the change-watcher both diff. It owns nothing
the run owns — the authoritative :class:`~yeaboi.agent.state.ShipRun` lives in
the store (``ShipStore``), written by the engine on its own thread — so the
board *reads* that run (cached, refreshed on the watcher thread) and merges in
the two live feeds it is handed directly:

- **progress components** (``note_component``) — the five-phase checklist, the
  same ``analysis_component`` events the TUI renders;
- **agent activity** (``note_agent_line``) — the driver's ``stream-json`` events,
  **filtered down to what is safe to show a remote watcher**.

Everything a stranger with the link could read is scrubbed at projection time
(``yeaboi.standup.gap_issues.scrub`` — ``$HOME`` → ``~`` plus secret redaction),
because a ship board discloses a private diff and shell output, not a team's own
retro cards. What is *never* projected: raw tool inputs (they carry file paths
and file contents) and tool-result events (command output before it is scrubbed
into the validation tail). That allowlist lives in :func:`_summarise_event`.

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

# Pipeline order the board projects phases in. The ids match the engine's
# _report() calls; the canonical human order is SHIP_PHASES in
# ui/mode_select/screens/_screens_ship.py, which the TUI walks. Duplicated here
# rather than imported so the web backend does not depend on ui/ — TestPhaseOrder
# in test_ship_board.py keeps the two tuples in lockstep.
_PHASE_ORDER = ("ship-setup", "ship-implement", "ship-validate", "ship-gate", "ship-finalize")

_ACTIVITY_MAX = 200  # bounded ring of agent-activity entries; a long run cannot grow it without bound
_TEXT_SNIPPET = 240  # chars of an assistant text block shown live (scrubbed); the full diff is the record
_PRESENCE_TTL_S = 12.0  # a watcher is "here" if it heartbeat within this window
_RUN_CACHE_TTL_S = 0.5  # least time between store reads on the watcher thread


def _scrub(text: str) -> str:
    """Home-path + secret scrub for anything a remote watcher may read.

    Reuses the publication scrub the PR body already goes through, with an empty
    name mask (the board has no team roster to anonymise) — so it collapses to
    ``$HOME`` → ``~`` and :func:`yeaboi.redaction.redact` over token/secret
    shapes. Imported lazily to keep board import cheap and the dependency local.
    """
    from yeaboi.standup.gap_issues import scrub  # noqa: PLC0415 — lazy, avoids an import cycle

    return scrub(text or "", {})


def _summarise_event(line: str) -> dict | None:
    """Project one ``stream-json`` line to a safe activity entry, or None.

    THE allowlist. A live watcher sees that the agent is thinking and which
    tools it is using — never *what* it is reading or writing:

    - ``assistant`` text block → a short, scrubbed snippet;
    - ``assistant`` ``tool_use`` block → the tool **name** only, never its input
      (a ``Read``/``Edit`` input is a path and file contents);
    - ``system`` init → the model id, so the header can show it;
    - everything else (``user``/tool-result events especially — they carry
      command output and file contents) → dropped.

    Malformed or partial JSON is dropped, never raised on — a UI feed must not
    be able to crash the pump thread that drives it.
    """
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind == "system":
        model = event.get("model")
        return {"kind": "system", "text": _scrub(str(model))} if isinstance(model, str) and model else None
    if kind != "assistant":
        # user / tool_result / result / anything else: not model-visible activity
        # that is safe to forward. The result envelope is read from the store.
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    blocks = message.get("content")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return {"kind": "text", "text": _scrub(text.strip())[:_TEXT_SNIPPET]}
        elif btype == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                # Name only. The input is deliberately discarded here.
                return {"kind": "tool", "name": name}
    return None


class ShipBoard:
    """Projected live view of one ship run for the browser board.

    Constructed with the run id and the store path; tolerates the run row not
    existing yet (the engine writes it at the setup phase, a beat after the id
    exists). Reads of the authoritative run happen on the watcher thread only
    (:meth:`revision` refreshes the cache); handler threads touch no database.
    """

    def __init__(
        self,
        run_id: str,
        *,
        db_path: Path | None = None,
        story_title: str = "",
        project_name: str = "",
    ) -> None:
        self.run_id = run_id
        self.story_title = story_title
        self.project_name = project_name
        self._db_path = db_path
        self._lock = threading.Lock()
        self._rev = 0
        self._components: dict[str, dict] = {}  # component_id -> the last event for it
        self._activity: deque[dict] = deque(maxlen=_ACTIVITY_MAX)
        self._presence: dict[str, dict] = {}  # pid -> {name, avatar, last_seen}
        self._run_json: dict = {}  # the last store read, already JSON-safe
        self._run_fetched_at = 0.0
        self._last_run_signal: tuple | None = None  # (updated_at, status) of the last read

    # -- live feeds --------------------------------------------------------

    def note_component(self, event: dict) -> None:
        """Record one ``analysis_component`` progress event (the phase checklist)."""
        cid = str(event.get("component_id", ""))
        if not cid:
            return
        with self._lock:
            self._components[cid] = dict(event)
            self._rev += 1

    def note_agent_line(self, line: str) -> None:
        """Feed one raw ``stream-json`` line; store only its safe projection."""
        entry = _summarise_event(line)
        if entry is None:
            return
        with self._lock:
            self._activity.append(entry)
            self._rev += 1

    def heartbeat(self, pid: str, *, name: str = "", avatar: str = "") -> None:
        """Record a watcher's presence. Does NOT bump the revision.

        Like the retro board: heartbeats fire ~1/s and bumping the revision on
        each would defeat change detection. Join/leave is surfaced through the
        watcher probe (:meth:`present_pids`) instead.
        """
        if not pid:
            return
        with self._lock:
            self._presence[pid] = {"name": name[:60], "avatar": avatar[:16], "last_seen": time.monotonic()}

    # -- watcher-thread reads ---------------------------------------------

    def revision(self) -> int:
        """Refresh the cached run from the store, then return the monotonic counter.

        Called by the :class:`~yeaboi.sharing.events.ChangeWatcher` (one thread).
        The store read lives here, throttled, so the busy handler threads never
        touch the database. A store-side change (diff attached, gate resolved by
        the host) bumps the same counter inside :meth:`_refresh_run`, so it wakes
        parked polls and — crucially — stays **monotonic**, which the browser
        store's stale-drop guard relies on. A missing/failed read leaves the last
        cache in place rather than blanking the board.
        """
        self._refresh_run()
        with self._lock:
            return self._rev

    def present_pids(self) -> tuple[str, ...]:
        """Sorted pids seen within the TTL — the watcher wakes on join/leave."""
        cutoff = time.monotonic() - _PRESENCE_TTL_S
        with self._lock:
            return tuple(sorted(pid for pid, p in self._presence.items() if p["last_seen"] >= cutoff))

    def _refresh_run(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._run_fetched_at < _RUN_CACHE_TTL_S and self._run_json:
                return
            self._run_fetched_at = now
        run_json = self._read_run_json()
        if run_json is not None:
            signal = (run_json.get("updated_at", ""), run_json.get("status", ""))
            with self._lock:
                self._run_json = run_json
                # A run transition bumps the same monotonic counter as the live
                # feeds, so the watcher wakes and the browser store never drops
                # the newer snapshot as stale.
                if signal != self._last_run_signal:
                    self._last_run_signal = signal
                    self._rev += 1

    def _read_run_json(self) -> dict | None:
        """Open a short-lived store, read this run, return it JSON-safe or None."""
        from dataclasses import asdict  # noqa: PLC0415

        from yeaboi.ship.store import ShipStore  # noqa: PLC0415 — lazy, avoids an import cycle

        try:
            store = ShipStore(self._db_path)
            try:
                run = store.get_run(self.run_id)
            finally:
                store.close()
        except Exception:  # a locked/again-busy db must never crash the board
            logger.debug("ship board: run read failed", exc_info=True)
            return None
        return asdict(run) if run is not None else {}

    # -- projection --------------------------------------------------------

    def state_snapshot(self, pid: str = "") -> dict:
        """The browser payload: run status, phases, scrubbed diff, activity.

        Runs on handler threads and touches no database — everything comes from
        the lock-guarded cache. Every field a stranger with the link could read
        is scrubbed here (:func:`_scrub`); raw tool inputs and command output
        never reach this dict (see :func:`_summarise_event`).
        """
        with self._lock:
            run = dict(self._run_json)
            # Project in pipeline order, not alphabetical: sorted() by id would
            # read finalize → gate → implement → setup → validate. Unreported
            # phases are simply absent; an unknown id sorts to the end.
            order = {cid: i for i, cid in enumerate(_PHASE_ORDER)}
            components = [
                self._components[k] for k in sorted(self._components, key=lambda cid: (order.get(cid, len(order)), cid))
            ]
            activity = list(self._activity)
            presence = self._presence_snapshot_locked()
            revision = self._rev
        return {
            "revision": revision,  # monotonic; the browser store's stale-drop cursor
            "run_id": self.run_id,
            "status": str(run.get("status") or "starting"),
            "story": self.story_title,
            "project": self.project_name,
            "phases": components,
            "activity": activity,
            "diff_stat": run.get("diff_stat") or "",
            "diff_text": _scrub(run.get("diff_text") or ""),
            "validation": self._validation_view(run.get("validation")),
            "cost_usd": float(run.get("cost_usd") or 0.0),
            "findings": [list(f) for f in run.get("transcript_findings") or ()],
            "pr_url": run.get("pr_url") or "",
            "gate_resolution": run.get("gate_resolution") or "",
            "gate_comment": _scrub(run.get("gate_comment") or ""),
            "rejection_count": int(run.get("rejection_count") or 0),
            "warnings": [_scrub(w) for w in run.get("warnings") or ()],
            "branch": run.get("branch") or "",
            "presence": presence,
        }

    def _validation_view(self, validation: object) -> dict:
        """The validation verdict for the gate — command shown, tail scrubbed."""
        v = validation if isinstance(validation, dict) else {}
        return {
            "configured": bool(v.get("configured")),
            "command": _scrub(str(v.get("command") or "")),
            "passed": bool(v.get("passed")),
            "exit_code": int(v.get("exit_code") if v.get("exit_code") is not None else -1),
            "output_tail": _scrub(str(v.get("output_tail") or "")),
        }

    def _presence_snapshot_locked(self) -> list[dict]:
        cutoff = time.monotonic() - _PRESENCE_TTL_S
        return [{"name": p["name"], "avatar": p["avatar"]} for p in self._presence.values() if p["last_seen"] >= cutoff]
