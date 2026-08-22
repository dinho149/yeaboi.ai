"""One editable share, from opening it to deciding what to keep.

Three jobs that belong together and nowhere else:

* build the :class:`~yeaboi.sharing.editable.EditableShare` for an artifact,
* record every accepted correction in the append-only log as it arrives,
* and, when the host is done, write the corrected artifact back.

Why recording and committing are separate
-----------------------------------------

Every accepted edit is persisted **immediately**, because losing somebody's
correction to a dropped connection would be the worst possible failure here and
the log is append-only, so writing to it is always safe.

Committing — appending a corrected row to the mode's own history — is a separate,
later decision, and deliberately not something the share screen's teardown does
on its own. That teardown also runs on Esc, on Back, and on an exception, and a
path that rewrites the host's stored report from a crash handler is not one
anybody asked for. The screen returns a count; the caller decides.

# See docs: "Session Management" — SQLite persistence
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from yeaboi.artifacts.edits import Edit
from yeaboi.artifacts.registry import spec_for
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref, base_hash, hash_ip

logger = logging.getLogger(__name__)


class EditableSession:
    """A correctable share plus the durability around it."""

    def __init__(
        self,
        artifact: Any,
        *,
        kind: str,
        db_path: Path,
        run_id: int = 0,
        session_id: str = "",
        engineer: str = "",
        history: tuple = (),
    ) -> None:
        from yeaboi.sharing.documents import editable_share

        self.kind = kind
        self.db_path = db_path
        self.run_id = run_id
        self.session_id = session_id
        self.ref = artifact_ref(kind, run_id=run_id, session_id=session_id, engineer=engineer)
        self._base_hash = base_hash(artifact)
        self.share = editable_share(artifact, kind=kind, ref=self.ref, history=history)
        self._leased = False
        self._replay()
        self._take_lease()

    # ── The lease ─────────────────────────────────────────────────────────
    #
    # Held for exactly as long as this session might commit, and read by anyone
    # who would otherwise rewrite the same stored artifact underneath it — today
    # that is a practice verdict arriving from Slack, the first writer this
    # module has ever had that is not on the other end of a keyboard. See the
    # Leases section of `artifacts/store.py` for why it defers rather than
    # refuses.

    def _take_lease(self) -> None:
        with ArtifactEditStore(self.db_path) as store:
            store.take_lease(self.kind, self.ref, holder=self.share.document.share_id)
        self._leased = True

    def close(self) -> None:
        """Release the lease. Idempotent, and safe to call after ``commit()``.

        Separate from ``commit()`` because a share is very often opened and then
        closed with nothing recorded — Esc, Back, or a reader who only looked —
        and a lease dropped only on commit would leak on every one of those.
        """
        if not self._leased:
            return
        self._leased = False
        with ArtifactEditStore(self.db_path) as store:
            store.release_lease(self.kind, self.ref)

    def __enter__(self) -> EditableSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _replay(self) -> None:
        """Rebuild the document from every correction already on record.

        Without this a session starts from the generated original with an empty
        log while the *store* keeps counting, so the two disagree — and the next
        commit writes an artifact missing every earlier correction while
        reporting success. The docstrings promise a document is always
        ``base + the log``; this is what makes that true across sessions and
        across separate headless calls, not just within one.

        Replayed through ``document.apply`` rather than by assignment so a stored
        edit that no longer fits — a member who left, a field since removed from
        the allowlist — is skipped exactly as it would be anywhere else, instead
        of being forced back in.
        """
        with ArtifactEditStore(self.db_path) as store:
            recorded = store.list_edits(self.kind, self.ref)
        for edit in recorded:
            try:
                self.share.document.apply(edit)
            except Exception as exc:  # noqa: BLE001 — a stale edit must not stop the share opening
                # Kept and shown, not dropped. `edits.py` promises a correction
                # that no longer fits is "shown as unapplied, which is the
                # honest outcome"; swallowing it here left the reader who wrote
                # it with a document missing their change and a history that
                # never mentioned it.
                self.share.document.record_unapplied(edit, str(exc))
                logger.warning("Recorded %s edit could not be replayed (%s): %s", self.kind, edit.edit_id[:8], exc)

    # ── While the share is open ───────────────────────────────────────────

    def persist(self, _share: Any, edit: Edit, ip: str) -> None:
        """Record one accepted edit. Wired as the server's ``on_edit``.

        Best-effort by contract — the server catches and logs anything raised
        here — because a correction that made it onto everyone's screen must not
        also fail their request just because the disk was busy.
        """
        with ArtifactEditStore(self.db_path) as store:
            store.record(
                edit,
                kind=self.kind,
                ref=self.ref,
                share_id=self.share.document.share_id,
                base=self._base_hash,
                ip_hash=hash_ip(ip, self.share.salt),
            )

    # ── Afterwards ────────────────────────────────────────────────────────

    def commit(self) -> int:
        """Append the corrected artifact to its mode's history, returning the row id.

        An **append**, never an update. `html_theme.history_series` already keeps
        the newest row per date and every `get_latest_*` orders by `run_at DESC`,
        so a corrected row supersedes its parent in every chart and every read
        with no read-path change at all — and the generated original survives,
        which is what makes a revert mean anything.

        Returns 0 when this artifact kind has no history table to append to
        (a team profile is an upsert; its only history is the edit log).
        """
        spec = spec_for(self.kind)
        if spec is None:
            self.close()
            return 0
        committer = _COMMITTERS.get(self.kind)
        if committer is None:
            logger.info("No history table for %s — corrections live in the edit log only", self.kind)
            self.close()
            return 0
        row_id = committer(self.db_path, self.share.document.current(), self.run_id, self.session_id)
        logger.info("Committed corrected %s as row %d (from %d)", self.kind, row_id, self.run_id)
        # The commit is the last thing this session can do to the stored
        # artifact, so nothing after it needs deferring on our account.
        self.close()
        return row_id


# Per-mode, because the seven stores are hand-copied clones with different
# signatures and a generic `update_run` would have to know all seven — which is
# the coupling those copies exist to avoid. Each of these is four lines over the
# store's own `record_run`.
#
# All four arguments are passed to every one even where a mode ignores some:
# uniform signatures are what let `_COMMITTERS` be a plain dict lookup, and the
# one that needs `session_id` is not obvious from the outside.


def _commit_standup(db_path: Path, artifact: Any, parent_id: int, _session_id: str) -> int:
    from yeaboi.standup.store import StandupStore

    with StandupStore(db_path) as store:  # the report carries its own session_id
        return store.record_run(artifact, origin="edited", edited_from_id=parent_id)


def _commit_reporting(db_path: Path, artifact: Any, parent_id: int, session_id: str) -> int:
    from yeaboi.reporting.store import ReportingStore

    # `session_id` is a *column*, not a field on the report — unlike standup and
    # retro, whose artifacts carry their own. Omitting it wrote the corrected row
    # with an empty session, and both `get_latest_report` and `get_history`
    # filter on it: the reporting hub went on showing the uncorrected run while
    # the corrected one sat in the table unreachable.
    with ReportingStore(db_path) as store:
        return store.record_run(artifact, session_id=session_id, origin="edited", edited_from_id=parent_id)


def _commit_retro(db_path: Path, artifact: Any, parent_id: int, _session_id: str) -> int:
    from yeaboi.retro.store import RetroStore

    with RetroStore(db_path) as store:  # the report carries its own session_id
        return store.record_run(artifact, origin="edited", edited_from_id=parent_id)


_COMMITTERS = {
    "standup": _commit_standup,
    "reporting": _commit_reporting,
    "retro": _commit_retro,
}
