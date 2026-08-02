"""The team's verdict on a practice signal: thumbs up, thumbs down, and why.

``habits.py`` is right often and wrong sometimes, and until now a wrong call was
unfixable. The reader had no way to say so, the signal stayed in the stored
report, and — because a pull request can sit open for a week — the same wrong
nudge fired again the next morning at a named person. Nothing the team knew ever
reached the detector.

A verdict is cast on a *signal* (that is what the reader sees) but remembered per
*change* (that is what actually recurs). One thumbs-down on "PR #42 and 2 other
changes carry no ticket reference" therefore excuses three changes, and tomorrow's
sentence is rebuilt from whatever survives instead of a whole rule going silent
for that person.

**Two effects, deliberately different in kind.**

- *Thumbs down* is deterministic and immediate: the signal is removed from the
  stored report now, and every handle behind it is excused forever after by
  ``habits._excuse`` — no model involved, no chance of the correction being
  forgotten or re-litigated.
- *Thumbs up* changes nothing about today's report, which is already correct. It
  only feeds the adjudication prompt, so the model learns not to excuse work like
  it.

**The suppress-only property survives both.** ``Adjudicator`` still answers with
ids to drop, so confirmations can make it drop *less* and nothing else; and the
excuse path can only remove. Neither direction gives the feedback loop a shape in
which it could invent a signal, so the deterministic rules in ``habits.py`` remain
the only thing that can accuse anyone of anything.

# See docs: "Daily Standup" — practices
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence

from yeaboi.agent.state import MemberUpdate, PracticeSignal, StandupReport
from yeaboi.standup import habits

logger = logging.getLogger(__name__)

VERDICT_UP = "up"
VERDICT_DOWN = "down"
VERDICTS = (VERDICT_UP, VERDICT_DOWN)

# What reaches the adjudication prompt. Corrections outnumber confirmations
# because they are the more informative half: a confirmation says "you were
# already right", a correction says where the line actually is. Both are capped
# so a team that votes every day cannot grow the prompt without bound.
_MAX_CORRECTIONS = 12
_MAX_CONFIRMATIONS = 6
_NOTE_CLIP = 200
_SUBJECT_CLIP = 120


@dataclasses.dataclass(frozen=True)
class FeedbackExample:
    """One past verdict, flattened for the prompt.

    Carries no handle and no member name: the model is being calibrated, not
    told who to go easy on, and a name in this payload would be a name it could
    echo into a judgement about someone else's change.
    """

    rule: str = ""
    verdict: str = ""
    subject: str = ""
    note: str = ""


@dataclasses.dataclass(frozen=True)
class Ledger:
    """Every verdict a session has recorded, in the two shapes callers need."""

    excused: frozenset[tuple[str, str]] = frozenset()  # (rule, handle) — thumbs down
    confirmed: frozenset[tuple[str, str]] = frozenset()  # (rule, handle) — thumbs up
    examples: tuple[FeedbackExample, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.excused or self.confirmed)

    def is_excused(self, rule: str, handle: str) -> bool:
        """The ``habits.Excuser`` seam — see the type's note on suppress-only."""
        return (rule, handle) in self.excused

    def corrections(self) -> tuple[dict, ...]:
        """The examples worth spending prompt tokens on, as the prompt payload.

        Only the two adjudicated rules: the other five never reach a model, so
        their verdicts are deterministic-only, and passing them would be teaching
        the adjudicator about questions it is never asked.

        Dicts rather than dataclasses because this is a wire shape — it is
        ``json.dumps``-ed straight into the prompt — and it deliberately drops
        the member name that ``FeedbackExample`` already refuses to carry.
        """
        adjudicated = (habits.RULE_UNTRACKED_WORK, habits.RULE_UNTRACKED_DOCS)
        picked: list[dict] = []
        for verdict, cap in ((VERDICT_DOWN, _MAX_CORRECTIONS), (VERDICT_UP, _MAX_CONFIRMATIONS)):
            matching = [e for e in self.examples if e.verdict == verdict and e.rule in adjudicated]
            picked.extend(
                {"verdict": e.verdict, "kind": e.rule, "subject": e.subject, "note": e.note} for e in matching[:cap]
            )
        return tuple(picked)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load(store, session_id: str) -> Ledger:
    """Read a session's ledger. Never raises — feedback is a nicety, the report is not."""
    try:
        rows = store.load_practice_feedback(session_id)
    except Exception:  # a missing table or a corrupt row must not fail a standup
        logger.warning("standup: could not read practice feedback — detection runs unadjusted", exc_info=True)
        return Ledger()

    excused: set[tuple[str, str]] = set()
    confirmed: set[tuple[str, str]] = set()
    examples: list[FeedbackExample] = []
    # One vote on a signal writes one row per change behind it — all of them
    # carrying that signal's subject and the one note the reader typed. Suppression
    # wants every row; the prompt wants the judgement once, or a verdict on a
    # five-commit signal would outvote four separate ones that disagree with it.
    seen_examples: set[FeedbackExample] = set()
    for row in rows:
        rule = str(row.get("rule") or "")
        handle = str(row.get("handle") or "")
        verdict = str(row.get("verdict") or "")
        if not rule or not handle or verdict not in VERDICTS:
            continue
        (excused if verdict == VERDICT_DOWN else confirmed).add((rule, handle))
        subject = _clip(str(row.get("subject") or ""), _SUBJECT_CLIP)
        note = _clip(str(row.get("note") or ""), _NOTE_CLIP)
        # An example with neither a subject nor a note would tell the model
        # nothing; the suppression above is the whole value of such a row.
        if not subject and not note:
            continue
        example = FeedbackExample(rule=rule, verdict=verdict, subject=subject, note=note)
        if example not in seen_examples:
            seen_examples.add(example)
            examples.append(example)
    ledger = Ledger(excused=frozenset(excused), confirmed=frozenset(confirmed), examples=tuple(examples))
    if ledger:
        logger.info(
            "standup: practice feedback ledger — %d excused, %d confirmed",
            len(ledger.excused),
            len(ledger.confirmed),
        )
    return ledger


def find_signal(report: StandupReport | None, member: str, rule: str) -> PracticeSignal | None:
    """The signal a verdict is about, or None if it is no longer in the report.

    ``(member, rule)`` is unique by construction — each rule contributes at most
    one signal per member — which is why no id had to be invented for it.
    """
    for update in getattr(report, "member_updates", ()) or ():
        if update.name != member:
            continue
        for signal in getattr(update, "practices", ()) or ():
            if signal.rule == rule:
                return signal
    return None


def _without_signal(report: StandupReport, member: str, rule: str) -> StandupReport:
    """The report with one signal removed and the rollup recomputed.

    The rollup counts *members* per rule, so dropping the last signal of a rule
    has to drop its chip too — recomputing through ``habits.rollup`` rather than
    decrementing keeps that arithmetic in the one place that owns it.
    """
    updates: list[MemberUpdate] = []
    for update in report.member_updates:
        if update.name == member:
            kept = tuple(s for s in (update.practices or ()) if s.rule != rule)
            update = dataclasses.replace(update, practices=kept)
        updates.append(update)
    rollup = habits.rollup({u.name: u.practices for u in updates if u.practices})
    return dataclasses.replace(report, member_updates=tuple(updates), practice_rollup=rollup)


def apply_verdict(
    store,
    *,
    session_id: str,
    member: str,
    rule: str,
    verdict: str,
    note: str = "",
    run_id: int | None = None,
) -> bool:
    """Record one verdict and, on a thumbs-down, rewrite the stored report.

    The single write path, shared by the TUI and the MCP tool, so the two can
    never drift on what a vote does. Returns False when the verdict names a
    signal that is not in the report — a stale screen or a run that was voted on
    twice — rather than writing a row about a change it could not identify.

    Args:
        run_id: the history row to correct; the session's latest run when None.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if rule not in habits.ALL_RULES:
        raise ValueError(f"unknown practice rule: {rule!r}")

    if run_id is None:
        run_id = store.get_latest_run_id(session_id)
    if run_id is None:
        logger.warning("standup: practice feedback for %s has no stored run to apply to", session_id)
        return False

    report = store.get_run_by_id(run_id)
    signal = find_signal(report, member, rule)
    if report is None or signal is None:
        logger.warning("standup: no %s signal for %s in run id=%s — verdict ignored", rule, member, run_id)
        return False
    if not signal.handles:
        # A signal from a report written before feedback existed. Hiding it now
        # while remembering nothing would be the worst of both: it would look
        # answered and come straight back tomorrow. Refuse the whole verdict
        # instead — ``votable`` keeps this off the TUI, but the MCP tool can
        # name any signal in any stored run.
        logger.warning(
            "standup: %s signal for %s predates feedback — nothing to remember, verdict ignored", rule, member
        )
        return False

    # The subject travels with the verdict so a correction can still be read
    # months later, once the report it came from is far down the history list.
    subject = signal.evidence[0][0] if signal.evidence else signal.title
    for handle in signal.handles:
        store.record_practice_feedback(
            session_id,
            rule=rule,
            handle=handle,
            verdict=verdict,
            note=note,
            member=member,
            subject=subject,
            standup_date=report.date,
        )
    logger.info(
        "standup: practice feedback %s on %s for %s — %d change(s) remembered",
        verdict,
        rule,
        member,
        len(signal.handles),
    )

    if verdict == VERDICT_DOWN:
        store.update_run_report(run_id, _without_signal(report, member, rule))
    return True


def votable(signals: Sequence[PracticeSignal]) -> tuple[PracticeSignal, ...]:
    """Signals a verdict can actually be recorded for.

    A signal from a report written before handles existed has nothing to
    remember, so offering a thumbs-down on it would silently do half the job.
    """
    return tuple(s for s in signals if s.handles)
