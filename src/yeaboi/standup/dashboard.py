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

#: Card key → the title every surface shows. ``member:<name>`` rows are titled
#: by the member's own name and so are absent here.
CARD_TITLES: dict[str, str] = {
    "summary": "Team Summary",
    "my_update": "My Update",
    "team": "Team",
    "conflicts": "Conflicts",
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
