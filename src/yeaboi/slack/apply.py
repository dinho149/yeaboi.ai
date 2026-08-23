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

And **a reply can add prose but never change prose**. A correction becomes an
``OP_NOTE`` — a paragraph beside the report with a name on it — through the same
validator, the same injection sweep and the same caps a teammate on the shared
document gets. ``set``, ``append``, ``remove``, ``field`` and ``revert`` are
unreachable from this package, and ``tests/unit/test_slack_ops.py`` asserts that
over the AST rather than trusting it: the failure mode of a convention is
silence, and a future path reaching for ``OP_SET`` would look reasonable in
review while quietly turning "add a note" into "rewrite the report".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yeaboi.slack.grammar import (
    ACT_CONTROL,
    ACT_CORRECTION,
    ACT_VERDICT,
    INTENT_DOWN,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_SKIP,
    INTENT_UP,
)
from yeaboi.slack.store import (
    MAX_CORRECTIONS_PER_DAY,
    OUTCOME_APPLIED,
    OUTCOME_DEFERRED,
    OUTCOME_REFUSED,
    InboundEvent,
    SlackStore,
)

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
    if event.act == ACT_CORRECTION:
        return _correction(event, anchor, db_path=db_path)
    # Recording the refusal with its reason beats a silent no-op nobody can
    # explain later — but `speak` stays off, because an act this build does not
    # know is not something to announce in a channel.
    return ApplyResult(False, f"{event.act!r} is not handled yet")


def _correction(event: InboundEvent, anchor, *, db_path: Path | None) -> ApplyResult:
    """A sentence somebody typed, as an attributed note on the run.

    **A Slack reply can add prose. It can never change generated prose.** The op
    is always ``note`` — ``set``, ``append``, ``remove``, ``field`` and
    ``revert`` are unreachable from this package, asserted by an AST test rather
    than left to convention — so the strongest thing free text can do is add a
    paragraph beside the report with a name on it, through the same validator,
    the same injection sweep and the same caps a teammate on the shared document
    gets.

    The note lands at document level and never against a member, because
    **Slack threads are flat**: every reply carries ``thread_ts = root_ts``, so
    typed text cannot say which of the signal replies above it is meant. A
    reaction is per-message and can; prose cannot, and guessing is the inference
    ``habits.py`` exists to refuse.
    """
    from yeaboi.artifacts.edits import OP_NOTE
    from yeaboi.artifacts.engine import HEADLESS_KINDS, apply_artifact_edits

    if anchor.artifact_kind not in HEADLESS_KINDS or not anchor.run_id:
        # Silent on purpose: this is true of *every* prose reply under such a
        # post, so speaking it would answer the whole conversation. The ledger
        # row is where an operator finds out it happened.
        return ApplyResult(False, "that post has nothing correctable behind it")
    text = (event.payload or "").strip()
    if not text:
        return ApplyResult(False, "nothing to record")
    if not event.reply_ts:
        return ApplyResult(False, "a correction needs the reply it came from")

    already, refused_before = _correction_counts(event, db_path=db_path)
    if already >= MAX_CORRECTIONS_PER_DAY:
        # Told once, then quiet. A cap that answers every over-limit reply is an
        # amplifier for exactly the thread argument it exists to bound.
        return ApplyResult(
            False,
            f"that post has taken its {MAX_CORRECTIONS_PER_DAY} corrections for today — the rest are in the app.",
            speak=not refused_before,
        )

    held = _leased(anchor, db_path=db_path)
    try:
        outcome = apply_artifact_edits(
            anchor.artifact_kind,
            [
                {
                    "op": OP_NOTE,
                    "path": "",
                    "value": text,
                    # Deterministic, so a replayed window re-applies nothing:
                    # `EditableDocument.apply` returns the stored copy for an
                    # edit id it has already seen. It is also the join back to
                    # the ledger row that holds the Slack-verified identity —
                    # `Edit.author` stays self-declared, because a field meaning
                    # "verified" for some rows and "typed" for others is worse
                    # than one that always means the weaker thing.
                    "edit_id": f"slack-{event.channel}-{event.reply_ts}",
                }
            ],
            session_id=anchor.session_id,
            run_id=anchor.run_id,
            author=_author(event, anchor, db_path=db_path),
            db_path=db_path,
        )
    except ValueError as exc:
        # "no stored standup to correct", or a kind that cannot be reached
        # headlessly. Not the author's fault and not their problem to fix.
        return ApplyResult(False, str(exc))

    if outcome.get("refused"):
        # The validator's own words: too long, too many line breaks, an
        # injection pattern, or a document already at MAX_ANNOTATIONS. Every one
        # of those is the author's prose being rejected, so every one is spoken.
        return ApplyResult(False, str(outcome["refused"][0].get("reason", "refused")), speak=True)
    if outcome.get("stale"):
        return ApplyResult(False, "the report moved under that correction", speak=True)
    if not outcome.get("applied"):
        return ApplyResult(False, "that correction did not apply")
    if held:
        return ApplyResult(
            True,
            "recorded — someone has this report open, so it may not show on their copy until they reopen it.",
            outcome=OUTCOME_DEFERRED,
            speak=True,
        )
    # Spoken, unlike a verdict: the ✅ needs `reactions:write` and is off by
    # default, and a write of somebody's own prose into a stored report with no
    # signal at all that it landed is the gesture-with-no-consequence this lane
    # keeps refusing to make.
    return ApplyResult(True, f"noted on today's {anchor.mode or anchor.artifact_kind}.", speak=True)


def _author(event: InboundEvent, anchor, *, db_path: Path | None = None) -> str:
    """The name that goes on the note.

    Three readings, weakest last: a name the poller already resolved, then the
    session's ``slack_identities`` binding, then the raw ``@U…``. The fallback
    is not a degraded mode — it is the honest one. The id is what Slack's
    servers attributed; a display name inferred from it would be a guess wearing
    a person's name, and this string ends up on a teammate's report.

    Only the binding a human wrote is ever promoted to a roster name, which is
    why ``identity.link`` validates against the roster on write and this reads
    with no validation at all.
    """
    from yeaboi.slack import identity

    return (
        event.member or identity.resolve(anchor.session_id, event.slack_user, db_path=db_path) or f"@{event.slack_user}"
    )


def _correction_counts(event: InboundEvent, *, db_path: Path | None) -> tuple[int, int]:
    """(corrections already applied on this anchor today, refusals already spoken).

    Rolling twenty-four hours rather than a calendar day: a cap with a midnight
    cliff hands back twenty fresh corrections at a moment nobody chose.
    Unreadable counts as at the cap — a bound we cannot check is one we have to
    assume, because the thing it guards against is unbounded writing.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    try:
        with SlackStore(db_path) as store:
            common = {"channel": event.channel, "anchor_ts": event.anchor_ts, "act": ACT_CORRECTION, "since": since}
            applied = store.settled_count(**common, outcomes=(OUTCOME_APPLIED, OUTCOME_DEFERRED))
            refused = store.settled_count(**common, outcomes=(OUTCOME_REFUSED,))
    except Exception:  # noqa: BLE001 — an uncheckable cap is a cap that holds
        logger.warning("slack: could not count corrections on %s", event.anchor_ts, exc_info=True)
        return MAX_CORRECTIONS_PER_DAY, 1
    return applied, refused


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
