"""The live, correctable state of one shared artifact.

This is to a shared document what :class:`~yeaboi.retro.board.RetroBoard` is to a
retro: the single source of truth while the share is open, guarded by one lock,
with a ``_revision`` counter that the long poll and the ETag both read. Following
that shape rather than inventing one means :mod:`yeaboi.sharing.live` and
:mod:`yeaboi.sharing.events` work here unchanged.

What it holds
-------------

A **base** artifact, which never changes, and an ordered **log** of validated
edits. The current document is always ``apply_edits(base, log)`` — recomputed,
never mutated in place. That costs a materialisation per change and buys the
property the whole feature rests on: any version is a prefix of the log, so
"show me what it looked like before Ada's edit" is a slice rather than an undo
stack.

It also holds presence, for the same reason the boards do: a document two people
are correcting at once should say so, or the second person's conflict arrives as
a surprise.

Attribution
-----------

``pid``, ``author`` and ``avatar`` are all client-asserted, exactly as on the
retro board. That is a real limit and it is handled by saying so in the UI rather
than by pretending otherwise: the only enforced claims here are the join code,
the token, and the host's admin token.

# See docs: "Guardrails" — token gating, untrusted browser input
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from yeaboi.artifacts.edits import Edit, EditError, apply_edits, clean_avatar, validate
from yeaboi.artifacts.registry import ArtifactSpec

logger = logging.getLogger(__name__)

MAX_EDITS = 500
"""How many corrections one share may accept.

Not a limit anyone should meet — a heavily corrected standup has a dozen — but a
bound on what a joiner with the link can do to a document, and on how long a
materialisation can take now that every change replays the whole log.
"""

PRESENCE_TTL = 12.0
"""Seconds a heartbeat keeps someone listed. Matches the boards."""

MAX_PRESENCE = 40


class ConflictError(Exception):
    """An edit was refused because the document moved underneath it.

    Distinct from :class:`~yeaboi.artifacts.edits.EditError`, which means the
    edit was never acceptable. This one means *try again* — the caller answers
    409 with current state, and the browser can re-read and re-apply.
    """


class EditableDocument:
    """One shared artifact, its correction log, and who is looking at it."""

    def __init__(self, artifact: Any, spec: ArtifactSpec, *, kind: str = "", ref: str = "", share_id: str = "") -> None:
        self._lock = threading.Lock()
        self._base = artifact
        self._spec = spec
        self.kind = kind or spec.kind
        self.ref = ref
        self.share_id = share_id
        self._edits: list[Edit] = []
        self._current = artifact
        self._revision = 0
        self._locked = False
        self._presence: dict[str, dict] = {}

    # ── Reading ───────────────────────────────────────────────────────────

    @property
    def revision(self) -> int:
        """Bumped on every change. The long poll's and the ETag's cursor."""
        with self._lock:
            return self._revision

    @property
    def locked(self) -> bool:
        """True once the host has frozen editing for this share."""
        with self._lock:
            return self._locked

    def current(self) -> Any:
        """The corrected artifact as it stands."""
        with self._lock:
            return self._current

    def base(self) -> Any:
        """The generated artifact the log was written against. Never changes."""
        return self._base

    def edits(self) -> tuple[Edit, ...]:
        """The correction log, in accept order."""
        with self._lock:
            return tuple(self._edits)

    def editors(self) -> tuple[str, ...]:
        """Distinct self-declared names that have edited. Never an identity claim."""
        with self._lock:
            return tuple(dict.fromkeys(e.author for e in self._edits if e.author))

    # ── Writing ───────────────────────────────────────────────────────────

    def apply(self, edit: Edit, *, if_revision: int = -1) -> Edit:
        """Validate and append one correction, returning it as stored.

        Raises :class:`~yeaboi.artifacts.edits.EditError` when the edit is not
        acceptable and :class:`ConflictError` when it is, but the document has
        moved since the editor read it.

        Validation happens **outside** the lock and re-materialisation inside it.
        Validation runs a regex-based injection check over untrusted text, and
        holding a lock across that would let one slow request stall every reader
        of the document — the same reason the boards never wrap a render in
        their lock.
        """
        stored = validate(edit, self._spec)

        with self._lock:
            if self._locked:
                raise EditError("editing has been closed for this document")
            if len(self._edits) >= MAX_EDITS:
                raise EditError("this document has reached its edit limit")
            if if_revision >= 0 and if_revision != self._revision:
                raise ConflictError("the document changed while you were editing")
            if stored.edit_id:
                # A retried POST — a dropped tunnel, a backgrounded phone.
                # Return the stored copy and change nothing, so the retry gets
                # the same answer the first attempt got rather than appending
                # the correction a second time.
                for existing in self._edits:
                    if existing.edit_id == stored.edit_id:
                        return existing

            stored = replace(stored, seq=len(self._edits) + 1)
            candidate = [*self._edits, stored]
            current, results = apply_edits(self._base, tuple(candidate), self._spec)
            outcome = results[-1]
            if not outcome.applied:
                # Refused *after* materialisation, so the log never records an
                # edit that does nothing. "conflict" is retryable; the rest are
                # the editor's own stale view of a document that has changed.
                if outcome.reason == "conflict":
                    raise ConflictError("someone else changed this first")
                raise EditError(f"could not be applied ({outcome.reason})")

            self._edits = candidate
            self._current = current
            self._revision += 1

        logger.info(
            "Edit applied: kind=%s op=%s path=%s seq=%d rev=%d",
            self.kind,
            stored.op,
            stored.path,
            stored.seq,
            self._revision,
        )
        return stored

    def set_locked(self, locked: bool) -> None:
        """Freeze or unfreeze editing. Host action, admin-gated by the server."""
        with self._lock:
            if self._locked == locked:
                return
            self._locked = locked
            self._revision += 1
        logger.info("Editing %s for %s", "locked" if locked else "unlocked", self.kind)

    def drop_last(self) -> Edit | None:
        """Remove the most recent edit outright. The host's escape hatch.

        The only operation here that is not append-only, and it exists for one
        situation the log cannot express: something was pasted in that should
        never have been recorded at all. A reader-facing *revert* appends
        instead, so the history keeps showing what happened.
        """
        with self._lock:
            if not self._edits:
                return None
            dropped = self._edits.pop()
            self._current, _ = apply_edits(self._base, tuple(self._edits), self._spec)
            self._revision += 1
        logger.info("Host dropped edit seq=%d from %s", dropped.seq, self.kind)
        return dropped

    # ── Presence ──────────────────────────────────────────────────────────

    def heartbeat(self, pid: str, *, name: str = "", avatar: str = "", editing: str = "") -> None:
        """Record that someone is here, and what they are editing.

        Deliberately does **not** bump ``revision``: heartbeats fire about once a
        second and bumping would defeat the change detection the long poll is
        built on. The watcher probes ``presence_list`` separately — the same
        arrangement, and for the same reason, as the retro board.
        """
        if not pid:
            return
        with self._lock:
            if pid not in self._presence and len(self._presence) >= MAX_PRESENCE:
                return
            self._presence[pid] = {
                "name": name[:60],
                # Validated, not merely truncated — see `clean_avatar`.
                "avatar": clean_avatar(avatar),
                "editing": editing[:400],
                "seen": time.monotonic(),
            }

    def presence_list(self, viewer: str = "") -> list[dict]:
        """Everyone seen within the TTL, oldest arrival first.

        **No pid crosses the wire.** A pid is the authorship key — it is what
        `mine` is computed from and what an owner check compares — so handing
        every reader everybody else's would let one of them claim another's
        edits. The retro board withholds them for the same reason. What a caller
        actually wants is "is this me", so that is what ships.
        """
        cutoff = time.monotonic() - PRESENCE_TTL
        with self._lock:
            stale = [pid for pid, row in self._presence.items() if row["seen"] < cutoff]
            for pid in stale:
                del self._presence[pid]
            return [
                {
                    "name": row["name"],
                    "avatar": row["avatar"],
                    "editing": row["editing"],
                    "mine": bool(viewer) and pid == viewer,
                }
                for pid, row in self._presence.items()
            ]

    # ── Snapshot ──────────────────────────────────────────────────────────

    def state_snapshot(self, pid: str = "", *, payload_for: Any = None) -> dict:
        """The full frame one browser gets.

        ``payload_for`` builds the rendered payload from the corrected artifact;
        it is injected rather than imported so this module never has to know
        which exporter belongs to which kind.

        The whole materialised payload goes out on every frame, and the client
        never applies an edit locally. That is more bytes than a patch stream and
        it removes an entire class of bug: there is one place that decides what
        the document says, and it is the same place for the shared page and the
        file on disk.
        """
        with self._lock:
            current = self._current
            edits = tuple(self._edits)
            revision = self._revision
            locked = self._locked

        frame: dict[str, Any] = {
            "revision": revision,
            "editable": not locked,
            "edits": [
                {
                    "id": e.edit_id,
                    "seq": e.seq,
                    "op": e.op,
                    "path": e.path,
                    "value": e.value,
                    "label": e.label,
                    "target": e.target,
                    "author": e.author,
                    "avatar": e.avatar,
                    "at": e.at,
                    # Whose edit this is, without ever putting a raw pid on the
                    # wire — the same trick the retro board's `mine` flag uses.
                    "mine": bool(pid) and e.pid == pid,
                }
                for e in edits
            ],
            "people": self.presence_list(pid),
        }
        if payload_for is not None:
            frame.update(payload_for(current))
        return frame

    def change_probe(self) -> tuple:
        """What :class:`~yeaboi.sharing.events.ChangeWatcher` polls.

        Presence rides along even though it does not bump ``revision``, so the
        "who else is here" row refreshes without an unrelated change happening.
        """
        return (self.revision, self.presence_list())


@dataclass(frozen=True)
class EditableShare:
    """One editable document plus the two things the server needs to serve it.

    ``args`` turns the *corrected* artifact into the keyword arguments its
    ``<mode>_export_args`` produces — the same builder the file export uses,
    which is what stops a shared correction and its downloaded copy from
    disagreeing about anything, chrome included. Injected rather than imported so
    this module never learns which exporter belongs to which mode.
    """

    document: EditableDocument
    args: Callable[[Any], dict]
    title: str
    source_mode: str
    #: Per-share salt for hashing client addresses. Regenerated every share so a
    #: reader cannot be followed from one document to the next.
    salt: str = field(default="", repr=False)

    def payload(self, artifact: Any) -> dict:
        """The ``{chrome, report}`` boot payload for one artifact."""
        from yeaboi.html_theme import export_payload

        return export_payload(**self.args(artifact))

    def snapshot(self, pid: str = "") -> dict:
        """The frame one browser gets, artifact payload included."""
        return self.document.state_snapshot(pid, payload_for=self.payload)

    def page_args(self, frame: dict) -> dict:
        """Keyword arguments for :func:`~yeaboi.html_theme.export_page`.

        Built from the args the exporter itself would use, plus the one boot key
        that turns the bundle's edit stack on. Going through ``export_page``
        rather than ``render_page`` is what keeps the served document and the
        downloaded file identical in everything but that key.
        """
        return {
            **self.args(self.document.current()),
            "extra_data": {
                "editing": {
                    "revision": frame["revision"],
                    "editable": frame["editable"],
                    "edits": frame["edits"],
                    "people": frame["people"],
                }
            },
        }
