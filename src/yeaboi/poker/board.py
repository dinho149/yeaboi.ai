"""The live, in-memory poker board — the single source of truth during a session.

Planning poker is collaborative: while the host's TUI is open, teammates POST
votes from their browsers (poker/server.py) on background HTTP threads, and the
TUI render loop reads the board every frame on the main thread. Two-thread
access means all state MUST be guarded — ``PokerBoard`` owns one
``threading.Lock`` (the same concurrency contract as retro/board.py):

  * State is only ever mutated while holding ``_lock``.
  * Readers assemble a *copy* inside the lock, then work on the copy outside —
    the lock is never held across a Rich render, a JSON dump, or (crucially)
    a tracker/LLM network call.
  * ``_revision`` is bumped on every mutation, giving both the browser poller
    and the TUI an O(1) "did anything change?" check.

Per-ticket phase machine::

    "voting" ──reveal()──▶ "revealed" ──finalize_current()──▶ next ticket ("voting")
        ▲                      │  ▲
        │                      │  └──close_duel()── "duel" ◀──open_duel()──┘
        └──── restart_vote() ──┘

The "duel" phase is the open floor: the lowest and highest voters argue their
estimates in timed turns while the debate is recorded. The duel *state*
(``_duel``) outlives the phase — after close_duel() the board is back in
"revealed" while transcription runs (status "transcribing" → "done"/"failed"),
so finalize/re-vote/AI stay available. A finalize while the duel is still
transcribing drops the transcript (the browser disables Finalize until
transcription lands; a raw-HTTP admin bypassing that loses only their own
transcript).

Vote secrecy is enforced HERE, server-side: before the reveal,
``state_snapshot`` only ever exposes *who* has voted (plus the viewer's own
value) — other participants' values never go on the wire, so no client can
peek. Raw pids never leave the board either (retro's ``mine`` pattern).

The frozen, serializable artifacts (``PokerVote``, ``PokerTicketResult``,
``PokerReport``) live in agent/state.py; this module owns only the mutable live
object and the board → report snapshot.

# See docs: "Session Management" — Poker mode artifacts
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import date, datetime, timezone

from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote

# Reuse retro's canonical avatar + theme sets — poker's browser page shares the
# join/profile/theme machinery, so the two modes must never drift apart.
from yeaboi.retro.board import AVATARS, RETRO_THEMES

logger = logging.getLogger(__name__)

# The estimation deck. Server-validated (LAN peers are untrusted): a vote is
# rejected unless it is exactly one of these strings. "?" = no idea,
# "☕" = need a break. Emitted into types/enums.ts by scripts/gen_web_types.py
# (with a --check in CI), so the browser's deck cannot drift from this one. It
# is deliberately NOT also shipped in page.py's boot payload — see the docstring
# on board_config(): that would give one tuple two sources of truth, and the
# island wins at runtime, so a stale bundle would offer a card the board refuses.
POKER_DECK: tuple[str, ...] = ("0", "1", "2", "3", "5", "8", "13", "21", "?", "☕")
# The numeric subset (floats) — what medians/suggestions are computed from.
NUMERIC_DECK: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0)

# The per-ticket phases (see the machine in the module docstring).
PHASE_VOTING = "voting"
PHASE_REVEALED = "revealed"
PHASE_DUEL = "duel"  # the open floor: low vs high voter argue their estimates

# Emitted into types/enums.ts by scripts/gen_web_types.py, so the browser's
# phase union cannot drift from this one. Order is the state machine's own:
# a ticket goes voting -> revealed, and optionally -> duel and back.
POKER_PHASES: tuple[str, ...] = (PHASE_VOTING, PHASE_REVEALED, PHASE_DUEL)

# The duel's own lifecycle, likewise generated. Named here rather than written
# inline at each assignment because the browser renders a different panel for
# each one, and a client that spells a status differently silently falls through
# to its error branch — which is what "failed" vs "error" cost once already.
DUEL_LIVE = "live"
DUEL_TRANSCRIBING = "transcribing"
DUEL_DONE = "done"
DUEL_FAILED = "failed"
DUEL_STATUSES: tuple[str, ...] = (DUEL_LIVE, DUEL_TRANSCRIBING, DUEL_DONE, DUEL_FAILED)

# Input caps — bound memory and blunt abuse from a LAN peer.
_MAX_TEXT = 4000  # ticket descriptions can be long, but not unbounded
_MAX_SUMMARY = 200
_MAX_AUTHOR = 60
_MAX_NOTE = 2000
_PRESENCE_TTL = 12.0  # seconds a participant stays "here" after their last heartbeat
_MAX_TIMER = 3600  # cap a shared countdown at one hour

# AI-perspective evidence caps + the confidence vocabulary the engine emits.
_AI_CONFIDENCE_LEVELS = ("high", "medium", "low")
_MAX_EVIDENCE_ITEMS = 3
_MAX_EVIDENCE_LEN = 200

# Duel (open the floor) caps. The transcript is spoken debate — larger than a
# note, still bounded so snapshots/exports stay sane.
_MAX_TRANSCRIPT = 6000
_DUEL_TURN_MIN = 15  # seconds per speaking turn
_DUEL_TURN_MAX = 600


def _ai_idle() -> dict:
    """The AI-perspective state between rounds — one definition for every reset site."""
    return {"pending": False, "note": "", "suggested": None, "confidence": "", "evidence": (), "from_llm": False}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snap_to_deck(value: float) -> float:
    """Snap an arbitrary number to the nearest numeric deck card (ties round up).

    Planning-poker convention: estimates land ON the deck, and when torn
    between two cards the team rounds up (uncertainty costs more, not less).
    """
    best = NUMERIC_DECK[0]
    for card in NUMERIC_DECK:
        if abs(card - value) < abs(best - value) or (abs(card - value) == abs(best - value) and card > best):
            best = card
    return best


def median_of(values: list[float]) -> float | None:
    """Median of a list (mean of the middle two for even counts); None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


class PokerBoard:
    """Thread-safe live voting state for one poker session.

    The HTTP server threads call :meth:`cast_vote` / the admin mutators; the
    TUI render thread calls :meth:`state_snapshot` once per frame. All state
    access is serialized through ``_lock``.

    ``tickets`` are the normalized dicts from poker/tickets.py, held as
    *mutable copies* inside the lock — they accumulate live results
    (final_points, accepted votes, AI notes, edits) during the session; the
    frozen conversion happens once at report time (:func:`board_to_report`).
    """

    def __init__(
        self,
        session_id: str,
        project_name: str = "",
        *,
        source: str = "",
        scope_label: str = "",
        tickets: list[dict] | None = None,
        scope=None,
    ) -> None:
        self.session_id = session_id
        self.project_name = project_name
        self.source = source
        self.scope_label = scope_label
        # The run's ProjectScope (context toggles + session narrowing) for the
        # AI perspective's history gather. Never serialized into a snapshot.
        self.scope = scope
        self.created_at = _now_iso()
        self._tickets: list[dict] = []
        for t in tickets or []:
            row = dict(t)
            # Session-time result fields, stamped by finalize_current().
            row.setdefault("initial_points", row.get("story_points"))
            row.setdefault("final_points", None)
            row.setdefault("estimated", False)
            row.setdefault("accepted_votes", [])
            row.setdefault("ai_note", "")
            # Duel (open the floor) results, stamped by finalize_current().
            row.setdefault("duel_transcript", "")
            row.setdefault("duel_low", "")
            row.setdefault("duel_high", "")
            # Per-ticket content revision for the client's peek cache: bumped
            # only when this ticket's displayed content changes (edit/finalize).
            # The board-wide _revision bumps on every vote/heartbeat, so keying
            # a content cache on it would refetch constantly for no reason.
            row.setdefault("rev", 0)
            self._tickets.append(row)
        self._index = 0
        self._phase = PHASE_VOTING
        self._votes: dict[str, str] = {}  # pid -> deck value (current ticket, current round)
        # AI-perspective state: pending guards double-clicks while the worker
        # thread runs; note/suggested land via set_ai_note() and every client
        # picks them up on its next poll (broadcast-by-polling, like the timer).
        self._ai: dict = _ai_idle()
        # Duel state (open the floor). None when no duel this round; see the
        # duel section below for the dict shape. The pids inside NEVER go on
        # the wire — state_snapshot ships a names-only projection.
        self._duel: dict | None = None
        # The host's mic, which records the session rather than one duel. Board
        # state, not duel state: it is armed before there is a duel to record,
        # and every participant is shown that it is on.
        self._room_mic = False
        self._notice = ""  # last tracker-write error, shown to the admin
        self._revision = 0
        self._lock = threading.Lock()
        self._presence: dict[str, dict] = {}  # pid -> {name, avatar, last_seen}
        # Every name that ever joined (pid -> name) — presence expires, but the
        # report's participants list should credit everyone who took part.
        self._all_participants: dict[str, str] = {}
        self._timer: dict = {"running": False, "end_epoch": None, "duration": 0}
        # Host-driven "global" state applied by every browser on its next poll —
        # identical semantics to retro/board.py (theme/music broadcast + lock).
        self._broadcast: dict = {"theme": None, "music": None}
        self._music_seq = 0
        self._locked = False

    # ── Voting ────────────────────────────────────────────────────────────

    def cast_vote(self, pid: str, value: str) -> bool:
        """Record one participant's secret vote. Returns True if accepted.

        Rejected when: unknown deck value (LAN peers untrusted), no pid, no
        tickets, board locked, or votes already revealed (no changing your
        card after seeing everyone else's).
        """
        if value not in POKER_DECK or not pid:
            return False
        with self._lock:
            if self._locked or self._phase != PHASE_VOTING or not self._tickets:
                return False
            self._votes[pid] = value
            self._revision += 1
        # Never log the value — votes are secret until the reveal.
        logger.info("poker board: vote cast — ticket=%s voters=%d", self._current_key(), len(self._votes))
        return True

    def clear_vote(self, pid: str) -> bool:
        """Withdraw a participant's vote (tap the same card again). True if removed."""
        with self._lock:
            if self._locked or self._phase != PHASE_VOTING:
                return False
            if pid not in self._votes:
                return False
            del self._votes[pid]
            self._revision += 1
        logger.info("poker board: vote cleared — ticket=%s voters=%d", self._current_key(), len(self._votes))
        return True

    def reveal(self) -> bool:
        """Flip the current ticket to the revealed phase (admin only, via server)."""
        with self._lock:
            if self._phase != PHASE_VOTING or not self._tickets:
                return False
            self._phase = PHASE_REVEALED
            self._revision += 1
            voters = len(self._votes)
        logger.info("poker board: votes revealed — ticket=%s voters=%d", self._current_key(), voters)
        return True

    def restart_vote(self) -> bool:
        """Clear the round and go back to voting (works before or after a reveal)."""
        with self._lock:
            if not self._tickets:
                return False
            self._cancel_duel_locked()
            self._votes.clear()
            self._phase = PHASE_VOTING
            # A new round invalidates the AI's take on the old spread.
            self._ai = _ai_idle()
            self._revision += 1
        logger.info("poker board: vote restarted — ticket=%s", self._current_key())
        return True

    def suggested_points(self) -> float | None:
        """Median of the numeric votes, snapped to the deck (None if no numeric votes).

        "?" and "☕" are opinions about the process, not the size — they are
        excluded. This prefills the admin's finalize input after a reveal.
        """
        with self._lock:
            values = [float(v) for v in self._votes.values() if v not in ("?", "☕")]
        med = median_of(values)
        return snap_to_deck(med) if med is not None else None

    def finalize_current(self, points: float) -> bool:
        """Stamp the agreed points on the current ticket and advance to the next.

        Called by the server ONLY AFTER the tracker write succeeded — the board
        must never claim an estimate the real board doesn't have. Stores the
        revealed round (with names resolved from presence) as the accepted
        votes, resets the phase, and moves to the next ticket (staying on the
        last one when the batch is done).
        """
        try:
            points = float(points)
        except (TypeError, ValueError):
            return False
        with self._lock:
            if self._phase != PHASE_REVEALED or not self._tickets:
                return False
            ticket = self._tickets[self._index]
            ticket["final_points"] = points
            ticket["story_points"] = points  # the chip everyone sees reflects the new value
            ticket["estimated"] = True
            ticket["rev"] = int(ticket.get("rev", 0)) + 1  # points/estimated chips changed — invalidate peek caches
            # Fold the AI's evidence + confidence into the persisted note so the
            # "why it said what it said" survives in every downstream surface
            # (export, history, MCP) with no schema change.
            note = self._ai["note"]
            if note and self._ai.get("evidence"):
                note += "\nEvidence: " + "; ".join(self._ai["evidence"])
            if note and self._ai.get("confidence"):
                note += f"\n(AI confidence: {self._ai['confidence']})"
            ticket["ai_note"] = note
            # A finished duel becomes part of the ticket's record — the debate
            # that produced the estimate travels with it (export/history/MCP).
            if self._duel is not None and self._duel["status"] == DUEL_DONE and self._duel["transcript"]:
                ticket["duel_transcript"] = self._duel["transcript"]
                ticket["duel_low"] = f"{self._duel['low']['name']} ({self._duel['low']['value']})"
                ticket["duel_high"] = f"{self._duel['high']['name']} ({self._duel['high']['value']})"
            self._duel = None
            ticket["accepted_votes"] = [
                {
                    "voter": self._presence.get(pid, {}).get("name") or self._all_participants.get(pid, "anon"),
                    "avatar": self._presence.get(pid, {}).get("avatar", ""),
                    "value": value,
                }
                for pid, value in self._votes.items()
            ]
            key = ticket.get("key", "")
            if self._index < len(self._tickets) - 1:
                self._index += 1
            self._votes.clear()
            self._phase = PHASE_VOTING
            self._ai = _ai_idle()
            self._notice = ""
            self._revision += 1
        logger.info("poker board: ticket finalized — key=%s points=%s", key, points)
        return True

    def goto_ticket(self, index: int) -> bool:
        """Jump to another ticket (admin prev/next/rail click). Resets the round.

        Finalized results stay stamped on their tickets — revisiting one just
        starts a fresh voting round on it.
        """
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        with self._lock:
            if not (0 <= index < len(self._tickets)):
                return False
            if index == self._index:
                return True
            self._cancel_duel_locked()
            self._index = index
            self._votes.clear()
            self._phase = PHASE_VOTING
            self._ai = _ai_idle()
            self._revision += 1
        logger.info("poker board: moved to ticket %d/%d — key=%s", index + 1, len(self._tickets), self._current_key())
        return True

    # ── Ticket editing (admin, after the tracker write succeeded) ─────────

    def apply_ticket_edit(
        self,
        ticket_key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        story_points: float | None = None,
        state: str | None = None,
        assignee: str | None = None,
        issue_type: str | None = None,
        acceptance: str | None = None,
    ) -> bool:
        """Mirror a successful tracker edit onto the live board.

        ``description`` is the plain edited text — it becomes both the raw and
        display variants (after an edit, plain text IS the source of truth; see
        poker/tickets.py for the per-tracker storage conversion).
        """
        with self._lock:
            ticket = next((t for t in self._tickets if t.get("key") == ticket_key), None)
            if ticket is None:
                return False
            if summary is not None:
                ticket["summary"] = summary.strip()[:_MAX_SUMMARY]
            if description is not None:
                text = description.strip()[:_MAX_TEXT]
                ticket["description"] = text
                ticket["description_text"] = text
            if story_points is not None:
                try:
                    ticket["story_points"] = float(story_points)
                except (TypeError, ValueError):
                    pass
            if state is not None:
                ticket["state"] = state.strip()[:_MAX_SUMMARY]
            if assignee is not None:
                ticket["assignee"] = assignee.strip()[:_MAX_SUMMARY]
            if issue_type is not None:
                ticket["type"] = issue_type.strip()[:_MAX_SUMMARY]
            if acceptance is not None:
                text = acceptance.strip()[:_MAX_TEXT]
                ticket["acceptance"] = text
                ticket["acceptance_text"] = text
            ticket["rev"] = int(ticket.get("rev", 0)) + 1  # content changed — invalidate peek caches
            self._revision += 1
        logger.info("poker board: ticket edited — key=%s", ticket_key)
        return True

    # ── Duel (open the floor) ─────────────────────────────────────────────

    def open_duel(self, turn_seconds: int) -> tuple[bool, str]:
        """Open the floor: the lowest and highest voters get timed turns to argue.

        Only from the revealed phase (the spread must be public), and only when
        at least two DISTINCT numeric votes exist — a duel needs disagreement.
        When several people share an extreme value, one is picked at random
        (``secrets.choice``, matching the module family's crypto-random style).
        The low voter speaks first (planning-poker convention: the cheap
        estimate explains what the expensive one might be missing), and each
        turn drives the shared countdown timer every browser already renders.

        Returns (ok, error) — error is a short human-readable reason on False.
        """
        try:
            turn_seconds = int(turn_seconds)
        except (TypeError, ValueError):
            return False, "invalid turn length"
        turn_seconds = max(_DUEL_TURN_MIN, min(turn_seconds, _DUEL_TURN_MAX))
        with self._lock:
            if self._locked or self._phase != PHASE_REVEALED or not self._tickets:
                return False, "reveal the votes first"
            if self._duel is not None and self._duel["status"] in (DUEL_LIVE, DUEL_TRANSCRIBING):
                return False, "a duel is already running"
            numeric = {pid: float(v) for pid, v in self._votes.items() if v not in ("?", "☕")}
            if len(set(numeric.values())) < 2:
                return False, "need two different numeric votes to duel"
            low_value = min(numeric.values())
            high_value = max(numeric.values())
            low_pid = secrets.choice(sorted(pid for pid, v in numeric.items() if v == low_value))
            high_pid = secrets.choice(sorted(pid for pid, v in numeric.items() if v == high_value))
            self._duel = {
                "low_pid": low_pid,  # pids stay board-internal — see state_snapshot
                "high_pid": high_pid,
                "low": self._duelist_locked(low_pid),
                "high": self._duelist_locked(high_pid),
                "turn": "low",
                "turn_no": 1,
                "turn_seconds": turn_seconds,
                "status": DUEL_LIVE,
                "recording": {"host": False, "low": False, "high": False},
                "transcript": "",
                "error": "",
            }
            self._phase = PHASE_DUEL
            self._timer = {"running": True, "end_epoch": time.time() + turn_seconds, "duration": turn_seconds}
            self._revision += 1
            low_name, high_name = self._duel["low"]["name"], self._duel["high"]["name"]
        # Names + duration only — the values are public post-reveal, but the
        # never-log-vote-values rule stays absolute.
        logger.info("poker board: duel opened — low=%s high=%s turn=%ds", low_name, high_name, turn_seconds)
        return True, ""

    def _duelist_locked(self, pid: str) -> dict:
        """Public projection of one duelist (name/avatar/vote value). Lock held."""
        return {
            "name": self._presence.get(pid, {}).get("name") or self._all_participants.get(pid, "anon"),
            "avatar": self._presence.get(pid, {}).get("avatar", ""),
            "value": self._votes.get(pid, ""),
        }

    def advance_turn(self) -> bool:
        """Hand the floor to the high voter (turn 2) and restart the turn timer."""
        with self._lock:
            if self._phase != PHASE_DUEL or self._duel is None or self._duel["turn"] != "low":
                return False
            self._duel["turn"] = "high"
            self._duel["turn_no"] = 2
            seconds = self._duel["turn_seconds"]
            self._timer = {"running": True, "end_epoch": time.time() + seconds, "duration": seconds}
            self._revision += 1
        logger.info("poker board: duel turn advanced — high voter has the floor")
        return True

    def close_duel(self) -> dict | None:
        """Close the floor: back to revealed, status → transcribing.

        Returns a copy of the duel state INCLUDING the pids and the current
        ticket index — the server's STT worker needs role→pid attribution and
        a race guard (was the ticket changed while transcription ran?). The
        copy never leaves the host process. None if no duel is live.
        """
        with self._lock:
            if self._phase != PHASE_DUEL or self._duel is None:
                return None
            self._phase = PHASE_REVEALED
            self._duel["status"] = DUEL_TRANSCRIBING
            self._duel["recording"] = {"host": False, "low": False, "high": False}
            self._timer = {"running": False, "end_epoch": None, "duration": 0}
            self._revision += 1
            info = {**self._duel, "ticket_index": self._index}
        logger.info("poker board: duel closed — transcribing")
        return info

    def _cancel_duel_locked(self) -> None:
        """Drop any duel state (re-vote / ticket change). Caller holds the lock.

        An in-flight STT worker discovers the cancel via set_duel_transcript's
        None-check — its result is simply dropped.
        """
        if self._duel is None:
            return
        self._duel = None
        if self._phase == PHASE_DUEL:
            self._phase = PHASE_REVEALED
            self._timer = {"running": False, "end_epoch": None, "duration": 0}

    def set_duel_transcript(self, transcript: str, *, error: str = "") -> None:
        """Land the assembled transcript (STT worker thread) — or the failure.

        No-op when the duel was cancelled while transcription ran (revote /
        goto / finalize) — the worker's result is stale and dropped.
        """
        clean = (transcript or "").strip()[:_MAX_TRANSCRIPT]
        with self._lock:
            if self._duel is None:
                return
            self._duel["transcript"] = clean
            self._duel["error"] = (error or "").strip()[:_MAX_NOTE]
            self._duel["status"] = DUEL_DONE if clean else DUEL_FAILED
            self._revision += 1
        # Content is participant speech — log the size only (never-log rule).
        logger.info("poker board: duel transcript %s — %d chars", "landed" if clean else "FAILED", len(clean))

    def set_duel_recording(self, source: str, flag: bool) -> bool:
        """Flag a recording source (host mic / a duelist's browser mic) on or off.

        Drives the "● REC" indicators every participant sees — recording must
        never be invisible. Only while the duel is live.
        """
        if source not in ("host", "low", "high"):
            return False
        with self._lock:
            if self._duel is None or self._duel["status"] != DUEL_LIVE:
                return False
            self._duel["recording"][source] = bool(flag)
            self._revision += 1
        logger.info("poker board: duel recording %s=%s", source, bool(flag))
        return True

    def set_room_mic(self, flag: bool) -> None:
        """Turn the host's session recording on or off.

        Unlike :meth:`set_duel_recording` this is not tied to a live duel — the
        host arms it whenever, and the light every participant sees comes from
        here as well as from the duel's own flags.
        """
        with self._lock:
            if self._room_mic == bool(flag):
                return
            self._room_mic = bool(flag)
            self._revision += 1
        logger.info("poker board: room mic %s", "on" if flag else "off")

    def duel_pid_role(self, pid: str) -> str:
        """Return "low"/"high" if ``pid`` is a duelist, else "". Upload auth check.

        The pids stay inside the board; the server passes the uploader's pid in
        and gets a role out — it never learns the duelists' pids itself.
        """
        with self._lock:
            if not pid or self._duel is None:
                return ""
            if pid == self._duel["low_pid"]:
                return "low"
            if pid == self._duel["high_pid"]:
                return "high"
            return ""

    def current_duel_transcript(self) -> str:
        """The finished transcript for the current round ("" unless status done)."""
        with self._lock:
            if self._duel is None or self._duel["status"] != DUEL_DONE:
                return ""
            return self._duel["transcript"]

    # ── AI perspective ────────────────────────────────────────────────────

    def set_ai_pending(self, flag: bool) -> bool:
        """Mark the AI perspective as in flight. Returns False if already pending.

        The False return is the double-click guard: the server only spawns a
        worker thread when this flips pending from False to True.
        """
        with self._lock:
            if flag and self._ai["pending"]:
                return False
            self._ai["pending"] = bool(flag)
            self._revision += 1
        return True

    def set_ai_note(
        self,
        note: str,
        suggested: float | None = None,
        *,
        confidence: str = "",
        evidence: tuple[str, ...] = (),
        from_llm: bool = False,
    ) -> None:
        """Land the AI's take (worker thread) — every client sees it next poll.

        ``confidence`` says how strongly the team's recorded history backs the
        suggestion; ``evidence`` lists the concrete data points the AI cited
        (calibration stats, delivered ticket keys, …). Both come from the
        engine already validated, but the board re-checks — it is the trust
        boundary for everything that goes on the wire.

        ``from_llm`` is false when the engine fell back, and the board says so
        rather than leaving the reader to guess: a deterministic note is the
        median restated, which the decision row already shows.
        """
        if confidence not in _AI_CONFIDENCE_LEVELS:
            confidence = ""
        clean_evidence = tuple((e or "").strip()[:_MAX_EVIDENCE_LEN] for e in evidence if (e or "").strip())
        clean_evidence = clean_evidence[:_MAX_EVIDENCE_ITEMS]
        with self._lock:
            self._ai = {
                "pending": False,
                "note": (note or "").strip()[:_MAX_NOTE],
                "suggested": suggested,
                "confidence": confidence,
                "evidence": clean_evidence,
                "from_llm": bool(from_llm),
            }
            self._revision += 1
        logger.info(
            "poker board: AI note set — ticket=%s suggested=%s confidence=%s evidence=%d",
            self._current_key(),
            suggested,
            confidence or "n/a",
            len(clean_evidence),
        )

    def current_ticket_and_votes(self) -> tuple[dict | None, dict[str, str]]:
        """Return (ticket copy, {voter name: value}) for the AI worker.

        Taken atomically so the worker reasons about the exact revealed round,
        with names resolved the same way finalize_current() records them.
        """
        with self._lock:
            if not self._tickets:
                return None, {}
            ticket = dict(self._tickets[self._index])
            votes = {
                (self._presence.get(pid, {}).get("name") or self._all_participants.get(pid, "anon")): value
                for pid, value in self._votes.items()
            }
            return ticket, votes

    def set_notice(self, text: str) -> None:
        """Record the last tracker-write error (shown on the TUI + admin toast)."""
        with self._lock:
            self._notice = (text or "").strip()[:_MAX_NOTE]
            self._revision += 1

    # ── Presence ──────────────────────────────────────────────────────────

    def heartbeat(self, pid: str, *, name: str = "", avatar: str = "") -> None:
        """Record that a participant is here. Called on the browser's ~1 s tick.

        Ephemeral — never persisted, and does NOT bump ``_revision`` (it fires
        constantly and would defeat change-detection). Same contract as retro.
        """
        if not pid:
            return
        avatar = avatar if avatar in AVATARS else ""
        clean_name = (name or "anon").strip()[:_MAX_AUTHOR] or "anon"
        with self._lock:
            prev = self._presence.get(pid)
            self._presence[pid] = {"name": clean_name, "avatar": avatar, "last_seen": time.monotonic()}
            self._all_participants[pid] = clean_name
        if prev is None:
            logger.info("poker board: participant joined — name=%s", clean_name)
        elif prev["name"] != clean_name:
            logger.info("poker board: participant renamed — %s -> %s", prev["name"], clean_name)

    def _active_presence_locked(self) -> list[tuple[str, dict]]:
        cutoff = time.monotonic() - _PRESENCE_TTL
        return [(pid, p) for pid, p in self._presence.items() if p["last_seen"] >= cutoff]

    def presence_list(self) -> list[dict]:
        """Return ``[{name, avatar}, …]`` for participants seen within the TTL."""
        with self._lock:
            return [{"name": p["name"], "avatar": p["avatar"]} for _pid, p in self._active_presence_locked()]

    # ── Shared timer (identical to retro) ─────────────────────────────────

    def start_timer(self, seconds: int) -> None:
        """Start a shared countdown of ``seconds`` (clamped 1..3600)."""
        seconds = max(1, min(int(seconds or 0), _MAX_TIMER))
        with self._lock:
            self._timer = {"running": True, "end_epoch": time.time() + seconds, "duration": seconds}
            self._revision += 1
        logger.info("poker board: timer started — %d s", seconds)

    def stop_timer(self) -> None:
        """Stop/clear the shared countdown."""
        with self._lock:
            self._timer = {"running": False, "end_epoch": None, "duration": 0}
            self._revision += 1
        logger.info("poker board: timer stopped")

    def _timer_locked(self) -> dict:
        # Include the server clock so clients can compute an offset and tick locally.
        return {**self._timer, "now_epoch": time.time()}

    # ── Host broadcast (theme / music) + voting lock (identical to retro) ──

    def set_broadcast_theme(self, theme: str) -> bool:
        """Force a theme on every browser. Returns True if accepted (a known theme)."""
        if theme not in RETRO_THEMES:
            return False
        with self._lock:
            self._broadcast["theme"] = theme
            self._revision += 1
        logger.info("poker board: host broadcast theme=%s", theme)
        return True

    def set_broadcast_music(self, *, playing: bool, channel: int) -> bool:
        """Broadcast a music command (play/stop + station) to every browser.

        ``seq`` makes each client apply a given command exactly once — the same
        broadcast-by-polling trick as retro.
        """
        from yeaboi.music import CHANNELS

        try:
            channel = int(channel)
        except (TypeError, ValueError):
            return False
        if not CHANNELS or not (0 <= channel < len(CHANNELS)):
            return False
        with self._lock:
            self._music_seq += 1
            self._broadcast["music"] = {"playing": bool(playing), "channel": channel, "seq": self._music_seq}
            self._revision += 1
        logger.info("poker board: host broadcast music — playing=%s channel=%d", bool(playing), channel)
        return True

    def set_locked(self, flag: bool) -> None:
        """Freeze (or unfreeze) voting for everyone."""
        with self._lock:
            self._locked = bool(flag)
            self._revision += 1
        logger.info("poker board: voting %s by host", "locked" if flag else "unlocked")

    # ── Reads ─────────────────────────────────────────────────────────────

    def revision(self) -> int:
        """Return the current mutation counter (cheap change-detection)."""
        with self._lock:
            return self._revision

    def _current_key(self) -> str:
        # Callers hold no lock — reading one str item is safe enough for logging.
        return self._tickets[self._index].get("key", "") if self._tickets else ""

    def progress(self) -> tuple[int, int]:
        """Return (estimated count, total tickets)."""
        with self._lock:
            return sum(1 for t in self._tickets if t.get("estimated")), len(self._tickets)

    def tickets_snapshot(self) -> list[dict]:
        """Return copies of the ticket dicts (safe outside the lock)."""
        with self._lock:
            return [dict(t) for t in self._tickets]

    def ticket_view(self, index: object) -> dict | None:
        """Read-only public projection of one ticket, for the rail's peek view.

        Any token-holder may read any ticket in the batch (the same audience
        that sees the live one), but ONLY display fields go on the wire —
        never round internals (accepted_votes, ai_note, duel record, the raw
        tracker payload). The board re-validates the index as the trust
        boundary: any non-int or out-of-range value returns None (→ 404).
        """
        try:
            index = int(index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        with self._lock:
            if not (0 <= index < len(self._tickets)):
                return None
            t = self._tickets[index]
            view = {
                "index": index,
                "rev": t.get("rev", 0),
                "key": t.get("key", ""),
                "summary": t.get("summary", ""),
                "description_text": t.get("description_text", ""),
                "acceptance_text": t.get("acceptance_text", ""),
                "type": t.get("type", ""),
                "story_points": t.get("story_points"),
                "state": t.get("state", ""),
                "assignee": t.get("assignee", ""),
                "url": t.get("url", ""),
                "estimated": bool(t.get("estimated")),
                "final_points": t.get("final_points"),
            }
        logger.info("poker board: ticket peeked — key=%s", view["key"])
        return view

    def participants_all(self) -> list[str]:
        """Every name that ever joined, first-seen order (feeds the report)."""
        with self._lock:
            names = list(self._all_participants.values())
        seen: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.append(name)
        return seen

    def state_snapshot(self, viewer_pid: str = "") -> dict:
        """Return the full live state for the browser poll in one atomic payload.

        VOTE SECRECY lives here: while ``phase == "voting"``, the payload
        carries only ``{name, avatar, voted}`` per active participant plus the
        viewer's own value (``mine_value``). Values (and the distribution /
        median / suggestion) appear only once revealed. Raw pids never go on
        the wire. Built under the lock (plain dict/list assembly only — the
        JSON dump happens in the server, outside it).
        """
        with self._lock:
            ticket = dict(self._tickets[self._index]) if self._tickets else None
            tickets_meta = [
                {
                    "key": t.get("key", ""),
                    "summary": t.get("summary", ""),
                    "estimated": bool(t.get("estimated")),
                    "final_points": t.get("final_points"),
                    "story_points": t.get("story_points"),
                    # Content revision — lets clients cache peeked ticket bodies
                    # (fetched via GET /api/ticket) and refetch only on change.
                    "rev": t.get("rev", 0),
                }
                for t in self._tickets
            ]
            active = self._active_presence_locked()
            if self._phase == PHASE_VOTING:
                votes_payload: list[dict] = [
                    {"name": p["name"], "avatar": p["avatar"], "voted": pid in self._votes} for pid, p in active
                ]
                distribution: dict[str, int] = {}
                median = suggestion = None
            else:
                votes_payload = [
                    {
                        "name": self._presence.get(pid, {}).get("name") or self._all_participants.get(pid, "anon"),
                        "avatar": self._presence.get(pid, {}).get("avatar", ""),
                        "value": value,
                    }
                    for pid, value in self._votes.items()
                ]
                # Deck-ordered {value: count}, empties dropped.
                distribution = {
                    card: n for card in POKER_DECK if (n := sum(1 for v in self._votes.values() if v == card))
                }
                numeric = [float(v) for v in self._votes.values() if v not in ("?", "☕")]
                median = median_of(numeric)
                suggestion = snap_to_deck(median) if median is not None else None
            estimated = sum(1 for t in self._tickets if t.get("estimated"))
            # Duel projection: names/avatars/values only — the duelist pids stay
            # board-internal. "mine_role" tells the viewing client whether IT is
            # a duelist (retro's `mine` pattern; sturdier than name-matching).
            duel = None
            if self._duel is not None:
                d = self._duel
                mine_role = ""
                if viewer_pid and viewer_pid == d["low_pid"]:
                    mine_role = "low"
                elif viewer_pid and viewer_pid == d["high_pid"]:
                    mine_role = "high"
                duel = {
                    "status": d["status"],
                    "turn": d["turn"],
                    "turn_no": d["turn_no"],
                    "turn_seconds": d["turn_seconds"],
                    "low": dict(d["low"]),
                    "high": dict(d["high"]),
                    "recording": dict(d["recording"]),
                    "transcript": d["transcript"],
                    "error": d["error"],
                    "mine_role": mine_role,
                }
            return {
                "revision": self._revision,
                "phase": self._phase,
                "ticket_index": self._index,
                "ticket_count": len(self._tickets),
                "ticket": ticket,
                "tickets_meta": tickets_meta,
                "votes": votes_payload,
                "mine_value": self._votes.get(viewer_pid, ""),
                "distribution": distribution,
                "median": median,
                "suggestion": suggestion,
                "ai": {**self._ai, "evidence": list(self._ai.get("evidence", ()))},
                "duel": duel,
                "progress": {"estimated": estimated, "total": len(self._tickets)},
                "presence": [{"name": p["name"], "avatar": p["avatar"]} for _pid, p in active],
                "timer": self._timer_locked(),
                "broadcast": {"theme": self._broadcast["theme"], "music": self._broadcast["music"]},
                "locked": self._locked,
                "room_mic": self._room_mic,
                "notice": self._notice,
            }


def board_to_report(board: PokerBoard, *, today: date | None = None) -> PokerReport:
    """Snapshot a live board into a frozen, persistable PokerReport."""
    tickets = []
    for t in board.tickets_snapshot():
        votes = tuple(
            PokerVote(voter=v.get("voter", ""), avatar=v.get("avatar", ""), value=v.get("value", ""))
            for v in t.get("accepted_votes", [])
        )
        tickets.append(
            PokerTicketResult(
                key=t.get("key", ""),
                url=t.get("url", ""),
                summary=t.get("summary", ""),
                description=t.get("description_text", "") or t.get("description", ""),
                state=t.get("state", ""),
                assignee=t.get("assignee", ""),
                initial_points=t.get("initial_points"),
                final_points=t.get("final_points"),
                estimated=bool(t.get("estimated")),
                votes=votes,
                ai_note=t.get("ai_note", ""),
                duel_transcript=t.get("duel_transcript", ""),
                duel_low=t.get("duel_low", ""),
                duel_high=t.get("duel_high", ""),
            )
        )
    return PokerReport(
        date=(today or date.today()).isoformat(),
        session_id=board.session_id,
        project_name=board.project_name,
        source=board.source,
        scope_label=board.scope_label,
        tickets=tuple(tickets),
        participants=tuple(board.participants_all()),
        generated_at=_now_iso(),
    )
