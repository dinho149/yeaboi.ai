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
from pathlib import Path

from yeaboi.slack.grammar import (
    ACT_CONTROL,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_SKIP,
)
from yeaboi.slack.store import InboundEvent

logger = logging.getLogger(__name__)


def apply_event(event: InboundEvent, *, db_path: Path | None = None) -> tuple[bool, str]:
    """Apply one authorised, in-grammar, non-stale event. (applied, detail).

    Never raises for anything the write path does — a refusal is a result, and
    the ledger records the reason. The caller has already checked the
    allowlist, the grammar and the anchor's age.
    """
    anchor = event.anchor
    if anchor is None:
        return False, "no anchor"
    if event.act == ACT_CONTROL:
        return _control(event, anchor, db_path=db_path)
    # Verdicts and corrections arrive in later phases; recording the refusal
    # with its reason is better than a silent no-op nobody can explain.
    return False, f"{event.act!r} is not handled yet"


def _control(event: InboundEvent, anchor, *, db_path: Path | None) -> tuple[bool, str]:
    from yeaboi.ceremonies.scheduler import next_occurrence
    from yeaboi.ceremonies.store import CeremonyStore

    if not anchor.ceremony:
        return False, "that post is not about a ceremony"

    with CeremonyStore(db_path) as store:
        current = store.get(anchor.session_id, anchor.ceremony)
        if current is None:
            return False, f"no ceremony named {anchor.ceremony!r} any more"

        if event.intent in (INTENT_PAUSE, INTENT_RESUME):
            enabled = event.intent == INTENT_RESUME
            if current.enabled == enabled:
                # Not a failure: somebody reacted to say what is already true.
                return True, f"{anchor.ceremony} was already {'running' if enabled else 'paused'}"
            store.set_enabled(anchor.session_id, anchor.ceremony, enabled)
            # The job stays installed either way. The engine's guard turns the
            # resulting drift into a recorded skipped_paused rather than a
            # surprise, and nothing here has to touch a plist.
            return True, f"{anchor.ceremony} {'resumed' if enabled else 'paused'} (its job is unchanged)"

        if event.intent == INTENT_SKIP:
            occurrence = next_occurrence(current)
            if not occurrence:
                return False, f"could not work out {anchor.ceremony}'s next run"
            try:
                store.set_skip_next(anchor.session_id, anchor.ceremony, occurrence)
            except ValueError as e:
                return False, str(e)
            return True, f"{anchor.ceremony} will skip its {occurrence} run"

    return False, f"unknown intent {event.intent!r}"
