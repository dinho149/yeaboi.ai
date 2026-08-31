"""The standup dashboard's card vocabulary — which cards a report earns.

Surface-neutral on purpose: the TUI page and the desktop dashboard must agree
on what cards exist, what they are called, and when one is shown, or the two
drift into different products. Presentation stays with each surface — nothing
here truncates, wraps or styles.

The card list is COMPUTED per report rather than being a static table: every
team member earns their own ``member:<name>`` row, and Conflicts, Transcript
Review and Notices appear only when there is something in them. An empty card
would advertise a feature rather than report a result.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Card key → the title every surface shows. ``member:<name>`` rows are titled
#: by the member's own name and so are absent here.
CARD_TITLES: dict[str, str] = {
    "summary": "Team Summary",
    "my_update": "My Update",
    "team": "Team",
    "conflicts": "Conflicts",
    "production": "Production",
    "activity": "Activity",
    "gaps": "Transcript Review",
    "schedule": "Schedule",
    "notices": "Notices",
}

#: The prefix that makes a card key a member sub-row.
MEMBER_PREFIX = "member:"


def other_members(data: dict) -> list:
    """Member updates excluding the standup user, whose card is ``my_update``."""
    report = data.get("report")
    if report is None:
        return []
    my_name = data.get("my_name", "")
    return [m for m in report.member_updates if m.name != my_name]


def member_active(member) -> bool:
    """True when the member has attributed activity today.

    Reports saved before ``activity_count`` existed deserialize with 0 for
    everyone — fall back to the summary text so old standups don't render the
    whole team as quiet.
    """
    if getattr(member, "activity_count", 0):
        return True
    return bool(member.summary) and member.summary != "No activity detected."


def card_order(data: dict) -> list[str]:
    """Return the ordered card keys for the current standup data.

    With no generated report yet only Schedule is available. With a report the
    standup user's own card is a top-level ``my_update`` row and everyone else
    lives under a single ``team`` row — expanded inline into ``member:<name>``
    sub-rows when ``data["team_expanded"]`` is set.
    """
    report = data.get("report")
    if report is None:
        return ["schedule"]
    order = ["summary", "my_update", "team"]
    if data.get("team_expanded"):
        order += [f"{MEMBER_PREFIX}{m.name}" for m in other_members(data)]
    # Only when a disagreement was actually detected — same earn-the-card rule
    # as "gaps" below.
    if getattr(report, "conflicts", ()):
        order.append("conflicts")
    # Earned the same way, and named "Production" rather than "Unplanned work":
    # whether an incident was planned for is a judgement this tool cannot make.
    if getattr(report, "ops_signals", ()):
        order.append("production")
    order += ["activity"]
    # A nudge IS a result ("3 standups went unchecked"), so it earns the card on
    # the same terms as an actual review rather than being an exception to them.
    if data.get("review") is not None or data.get("nudge"):
        order.append("gaps")
    order += ["schedule"]
    if report.warnings:
        order.append("notices")
    return order


def card_title(key: str, data: dict | None = None) -> str:
    """Human title for a card key; member sub-rows are just the member's name."""
    if key.startswith(MEMBER_PREFIX):
        return key[len(MEMBER_PREFIX) :]
    return CARD_TITLES.get(key, key)


def cards(data: dict) -> list[dict]:
    """The dashboard as ``[{key, title, member}]`` — the desktop's card list."""
    out: list[dict] = []
    for key in card_order(data):
        member = key[len(MEMBER_PREFIX) :] if key.startswith(MEMBER_PREFIX) else ""
        out.append({"key": key, "title": card_title(key, data), "member": member})
    return out


def collect(session_id: str = "", *, db_path=None, message: str = "", run_id: int = 0) -> dict:
    """Everything a standup dashboard draws, for one session.

    ``session_id`` blank targets the most recently modified session, which is
    what a page opened from the home screen means. ``run_id`` opens one past
    run from the saved-runs hub instead of the latest. Every read is defensive:
    a dashboard that cannot reach one store should still show the rest, so a
    failure warns and leaves that key at its empty default.
    """
    from yeaboi.config import get_standup_user_name
    from yeaboi.paths import get_db_path

    db_path = db_path or get_db_path()
    data: dict = {
        "message": message,
        "session_id": session_id,
        "session_name": "",
        "my_name": get_standup_user_name(),
        "config": None,
        "report": None,
        "schedule": {},
        "review": None,
        "nudge": None,
        "gap_issues": [],
        "history": [],
        "run_id": run_id,
    }
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(db_path) as store:
            if not session_id:
                session_id = store.get_latest_session_id()
                if not session_id:
                    return data
                data["session_id"] = session_id
            meta = store.get_session(session_id) or {}
            data["session_name"] = make_display_name(meta) if meta else session_id
    except Exception:
        logger.warning("standup: failed to resolve session", exc_info=True)
        return data

    try:
        from yeaboi.standup import transcripts as _transcripts
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            data["config"] = store.load_config(session_id)
            data["report"] = store.get_run_by_id(run_id) if run_id else store.get_latest_report(session_id)
            data["history"] = store.get_history(session_id, limit=30)
            # The most recent transcript review + the gap→issue ledger, so the
            # Transcript Review card can show which gaps are already filed.
            data["review"] = store.get_latest_review(session_id)
            data["gap_issues"] = store.get_gap_issues(limit=50)
        # Two indexed SELECTs, once per page load (never per frame): which
        # standups ran without ever being checked against their meeting.
        data["nudge"] = _transcripts.transcript_nudge(session_id, config=data.get("config"), db_path=db_path)
        # The engine resolves "Me" to the user's real tracker identity (e.g. their
        # Jira displayName) — the report's my_name drives the "My Update" row.
        if data["report"] is not None and data["report"].my_name:
            data["my_name"] = data["report"].my_name
    except Exception:
        logger.warning("standup: failed to load standup store data", exc_info=True)
    try:
        from yeaboi.ceremonies.scheduler import get_schedule_status

        data["schedule"] = get_schedule_status(session_id)
    except Exception:
        logger.warning("standup: failed to read schedule status", exc_info=True)
    return data
