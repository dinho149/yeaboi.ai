"""Turn an authorised event into a call to a write path that already exists.

Nothing here invents a way to change anything. A control act calls the same
``CeremonyStore`` methods the CLI does; the identity it acts on comes entirely
from the anchor, never from what anybody typed.

**Slack never touches launchd or crontab.** The CLI and the TUI both uninstall
the OS job when they pause a ceremony, and a chat message must not: an OS write
driven from a channel is the sharpest privilege in this lane, and it does not
need to exist. A store-only pause is enough, because the engine's own guard
already returns ``skipped_paused`` when the store and the operating system have
drifted, announces it, and the drift line on the ceremonies page and in
``ceremonies list`` makes it visible. So the strongest thing a reaction can do
is set a boolean somebody can see and reverse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from yeaboi.slack.grammar import (
    ACT_CONTROL,
    ACT_VERDICT,
    INTENT_DOWN,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_SKIP,
    INTENT_UP,
)
from yeaboi.slack.store import OUTCOME_DEFERRED, InboundEvent

logger = logging.getLogger(__name__)

#: A thumb, in ``practice_feedback``'s vocabulary.
_VERDICTS = {INTENT_UP: "up", INTENT_DOWN: "down"}


@dataclass(frozen=True)
class ApplyResult:
    """What happened, in the three parts the caller needs separately.

    ``applied`` decides the ✅. ``outcome`` overrides the ledger word for the
    case a boolean cannot express — a deferral is applied *and* worth its own
    name. ``speak`` decides whether the human is told in the thread, and it is
    deliberately not "every refusal": an act a phase has not built yet refuses
    on every ordinary sentence in the thread, and a bot that answers all of them
    is one nobody leaves switched on.
    """

    applied: bool = False
    detail: str = ""
    outcome: str = ""
    speak: bool = False


def apply_event(event: InboundEvent, *, db_path: Path | None = None) -> ApplyResult:
    """Apply one authorised, in-grammar, non-stale event.

    Never raises for anything the write path does — a refusal is a result, and
    the ledger records the reason. The caller has already checked the
    allowlist, the grammar and the anchor's age.
    """
    anchor = event.anchor
    if anchor is None:
        return ApplyResult(False, "no anchor")
    if event.act == ACT_CONTROL:
        return _control(event, anchor, db_path=db_path)
    if event.act == ACT_VERDICT:
        return _verdict(event, anchor, db_path=db_path)
    # Corrections arrive in phase 6. Recording the refusal with its reason beats
    # a silent no-op nobody can explain later — but `speak` stays off, because
    # every ordinary sentence in the thread lands here.
    return ApplyResult(False, f"{event.act!r} is not handled yet")


def _verdict(event: InboundEvent, anchor, *, db_path: Path | None) -> ApplyResult:
    """A thumb on one practice signal.

    Every argument ``apply_verdict`` takes comes from the anchor. The reacting
    user's identity is not among them, and must not be: a signal is a claim
    about a *change*, so anyone the allowlist trusts can say the detector got it
    wrong. That is the whole feedback loop, and it is why this act needs no
    Slack-user-to-roster mapping at all.
    """
    if not anchor.is_signal:
        return ApplyResult(
            False,
            "a thumb on the post can't say which signal it means — react 👍 or 👎 on the signal's own reply.",
            speak=True,
        )
    verdict = _VERDICTS.get(event.intent, "")
    if not verdict:
        return ApplyResult(False, f"unknown verdict {event.intent!r}")
    if anchor.artifact_kind != "standup" or not anchor.run_id:
        return ApplyResult(False, "that signal is not attached to a stored run")

    from yeaboi.standup import practice_feedback
    from yeaboi.standup.store import StandupStore

    held = _leased(anchor, db_path=db_path)
    with StandupStore(db_path) as store:
        ok = practice_feedback.apply_verdict(
            store,
            session_id=anchor.session_id,
            member=anchor.member,
            rule=anchor.rule,
            verdict=verdict,
            run_id=anchor.run_id,
            rewrite_report=not held,
        )
    if not ok:
        return ApplyResult(False, "that signal is no longer in the report — it may have been answered already.")
    if held:
        return ApplyResult(
            True,
            "recorded — someone is correcting this report right now, "
            "so it drops out from the next run rather than immediately.",
            outcome=OUTCOME_DEFERRED,
            speak=True,
        )
    return ApplyResult(True, f"{verdict} on {anchor.rule} for {anchor.member}")


def _leased(anchor, *, db_path: Path | None) -> bool:
    """True while somebody has this run open in an editable share.

    A read, never a refusal: the durable half of the verdict lands either way.
    An unreadable lease reads as free, because deferring on a store error would
    make a vote quietly weaker for a reason nobody could see.
    """
    from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref
    from yeaboi.paths import get_db_path

    try:
        ref = artifact_ref(anchor.artifact_kind, run_id=anchor.run_id, session_id=anchor.session_id)
        with ArtifactEditStore(db_path or get_db_path()) as store:
            return store.lease_held(anchor.artifact_kind, ref)
    except Exception:  # noqa: BLE001 — a lease we cannot read must not weaken a vote
        logger.warning("slack: could not read the lease on %s run %s", anchor.artifact_kind, anchor.run_id)
        return False


def _control(event: InboundEvent, anchor, *, db_path: Path | None) -> ApplyResult:
    from yeaboi.ceremonies.scheduler import next_occurrence
    from yeaboi.ceremonies.store import CeremonyStore

    if not anchor.ceremony:
        return ApplyResult(False, "that post is not about a ceremony")

    with CeremonyStore(db_path) as store:
        current = store.get(anchor.session_id, anchor.ceremony)
        if current is None:
            return ApplyResult(False, f"no ceremony named {anchor.ceremony!r} any more")

        if event.intent in (INTENT_PAUSE, INTENT_RESUME):
            enabled = event.intent == INTENT_RESUME
            if current.enabled == enabled:
                # Not a failure: somebody reacted to say what is already true.
                return ApplyResult(True, f"{anchor.ceremony} was already {'running' if enabled else 'paused'}")
            store.set_enabled(anchor.session_id, anchor.ceremony, enabled)
            # The job stays installed either way. The engine's guard turns the
            # resulting drift into a recorded skipped_paused rather than a
            # surprise, and nothing here has to touch a plist.
            return ApplyResult(True, f"{anchor.ceremony} {'resumed' if enabled else 'paused'} (its job is unchanged)")

        if event.intent == INTENT_SKIP:
            occurrence = next_occurrence(current)
            if not occurrence:
                return ApplyResult(False, f"could not work out {anchor.ceremony}'s next run")
            try:
                store.set_skip_next(anchor.session_id, anchor.ceremony, occurrence)
            except ValueError as e:
                return ApplyResult(False, str(e))
            return ApplyResult(True, f"{anchor.ceremony} will skip its {occurrence} run")

    return ApplyResult(False, f"unknown intent {event.intent!r}")
