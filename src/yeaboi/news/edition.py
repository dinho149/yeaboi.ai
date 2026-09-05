"""The paper as an edition: the stories in reading order, and the words around them.

The desktop computes the same rules in its renderer (``lib/news/{paper,edition,
turn,time,masthead}.ts``); this is the terminal's copy, so both surfaces print
the same edition from the same paper. Pure over its inputs: every clock is a
parameter, and nothing here touches the network or the disk.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from yeaboi.news.paper import COLUMN_ORDER, Paper
from yeaboi.news.parse import NewsItem
from yeaboi.news.topics import PERSONA_BY_COLUMN, PERSONAS
from yeaboi.timeparse import parse_datetime

# Stories the page turns through before it comes round again.
EDITION_SIZE = 12
# How long a story stays up before the page turns.
PAGE_TURN_SECONDS = 12.0

KICKERS: dict[str, str] = {
    "yeaboi": "From yeaboi",
    "ai": "From the AI desk",
    "engineering": "From the engineering desk",
}
INSIDE_TITLE = "Inside this edition"
EMPTY_LINE = "Nothing to read yet."
OFF_LINE = "News is off, showing yeaboi alone."
REFRESHING_LINE = "Refreshing."

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SMALL = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_LONG_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# The picture beside a story: which scene the persona stands in, and its caption.
SCENE_BY_TOPIC: dict[str, str] = {
    "security": "vault",
    "policy": "chamber",
    "compute": "launchpad",
    "media": "stage",
    "models": "observatory",
    "research": "chalkboard",
    "tooling": "bench",
    "howto": "kitchen",
    "general": "newsstand",
}
SCENE_BY_KIND: dict[str, str] = {"release": "dock", "video": "studio"}
DEFAULT_SCENE = "newsstand"
CAPTIONS: dict[str, str] = {
    "vault": "The detective, outside the vault.",
    "chamber": "The martial artist, before the chamber.",
    "launchpad": "The astronaut, at the pad.",
    "stage": "The DJ, on stage.",
    "observatory": "The wizard, under the stars.",
    "chalkboard": "The teacher, at the board.",
    "bench": "The engineer, at the bench.",
    "kitchen": "The chef, at the counter.",
    "newsstand": "The morning papers, at the stand.",
    "dock": "The wizard, at the dock, with the crates.",
    "studio": "The DJ, on the mark.",
}


@dataclass(frozen=True)
class Page:
    """One story as the strip prints it."""

    item: NewsItem
    kicker: str = ""
    counter: str = ""
    byline: str = ""
    read: str = ""
    persona: str = ""
    scene: str = ""
    caption: str = ""


def _known(column: str) -> bool:
    return column in COLUMN_ORDER


def stories(paper: Paper, *, size: int = EDITION_SIZE) -> tuple[NewsItem, ...]:
    """The edition in reading order: the lead, then the desks round-robin, each story once, capped."""
    seen: set[str] = set()
    out: list[NewsItem] = []

    def take(item: NewsItem) -> None:
        if item.id in seen or len(out) >= size:
            return
        seen.add(item.id)
        out.append(item)

    if paper.lead is not None and _known(paper.lead.column):
        take(paper.lead)
    by_column = {section.column: list(section.items) for section in paper.sections}
    queues = [by_column.get(column, []) for column in COLUMN_ORDER]
    while any(queues) and len(out) < size:
        for queue in queues:
            if queue:
                take(queue.pop(0))
    return tuple(out)


def is_empty(paper: Paper) -> bool:
    """Whether the paper has anything to print at all."""
    if paper.lead is not None and _known(paper.lead.column):
        return False
    return all(not _known(section.column) or not section.items for section in paper.sections)


def turn_index(elapsed: float, period: float, offset: int, count: int) -> int:
    """Which story is up after ``elapsed`` seconds on the clock and ``offset`` turns by hand."""
    if count <= 0:
        return 0
    ticks = int(max(0.0, elapsed) // period) if period > 0 else 0
    return (ticks + offset) % count


def kicker(column: str) -> str:
    return KICKERS.get(column, "")


def scene_for(item: NewsItem) -> str:
    """The scene a story is pictured in: by kind first, then by topic."""
    return SCENE_BY_KIND.get(item.kind) or SCENE_BY_TOPIC.get(item.topic, DEFAULT_SCENE)


def caption_for(scene: str) -> str:
    return CAPTIONS.get(scene, CAPTIONS[DEFAULT_SCENE])


def persona_of(item: NewsItem) -> str:
    """Which costume the duck wears for a story: the wire's, else the desk's, else the engineer."""
    if item.persona in PERSONAS:
        return item.persona
    return PERSONA_BY_COLUMN.get(item.column, "engineer")


def dateline(now: datetime) -> str:
    """``Saturday, 5 September 2026``."""
    return f"{_WEEKDAYS[now.weekday()]}, {now.day} {_LONG_MONTHS[now.month - 1]} {now.year}"


def volume_line(version: str) -> str:
    """``Vol. 3, No. 41`` from the package version; "" when it does not parse."""
    match = re.match(r"^(\d+)\.(\d+)", version.strip())
    return f"Vol. {int(match.group(1))}, No. {int(match.group(2))}" if match else ""


def source_tag(item: NewsItem) -> str:
    return item.source_name or "yeaboi"


def read_label(item: NewsItem) -> str:
    """Where the story opens, in words."""
    if item.kind == "video":
        return "Watch on YouTube"
    if item.kind == "release":
        return "Read the release notes"
    if item.source_id == "yeaboi-site":
        return "Read more on yeaboi.ai"
    return f"Read more at {source_tag(item)}"


def short_date(then: datetime, now: datetime) -> str:
    """``4 Sep``, or ``4 Sep 2025`` when the year is not this one."""
    label = f"{then.day} {_MONTHS[then.month - 1]}"
    return label if then.year == now.year else f"{label} {then.year}"


def relative_time(iso: str, now: datetime) -> str:
    """How long ago, in the paper's voice. An unparseable stamp comes back as is; an empty one as ""."""
    if not iso:
        return ""
    try:
        then = parse_datetime(iso)
    except (TypeError, ValueError):
        return iso
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    then = then.astimezone(now.tzinfo or timezone.utc)
    days = (now.date() - then.date()).days
    if days <= 0:
        ago = max(0.0, (now - then).total_seconds())
        if ago < 60:
            return "just now"
        if ago < 3600:
            minutes = int(ago // 60)
            return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
        hours = int(ago // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    if days == 1:
        return "yesterday"
    if days < 7:
        return then.strftime("%A")
    return short_date(then, now)


def byline(item: NewsItem, now: datetime) -> str:
    """``Techmeme, 2 hours ago``, or the outlet alone when the time is unknown."""
    when = relative_time(item.published, now)
    tag = source_tag(item)
    return f"{tag}, {when}" if when else tag


def counter(index: int, count: int) -> str:
    """``2 of 8``; nothing when there is nothing to turn to."""
    return "" if count <= 1 else f"{index + 1} of {count}"


def inside_label(count: int) -> str:
    """The folded index line; nothing with fewer than two other stories."""
    if count <= 2:
        return ""
    more = count - 1
    return f"{INSIDE_TITLE}, {more} more {'story' if more == 1 else 'stories'}"


def edition_line(paper: Paper, now: datetime, *, enabled: bool) -> str:
    """What the paper in hand is: yeaboi alone, being refreshed, or refreshed when."""
    if not enabled:
        return OFF_LINE
    if paper.stale:
        return REFRESHING_LINE
    when = relative_time(paper.generated_at, now)
    return f"Refreshed {when}." if when else "Refreshed."


def sources_line(paper: Paper) -> str:
    """``Read from 14 outlets.``, ``Read from 12 outlets, two not answering.``, or "" with none."""
    if not paper.sources:
        return ""
    ok = sum(1 for status in paper.sources if status.ok)
    down = len(paper.sources) - ok
    outlets = "1 outlet" if ok == 1 else f"{ok} outlets"
    if down == 0:
        return f"Read from {outlets}."
    spelled = _SMALL[down] if down < len(_SMALL) else str(down)
    return f"Read from {outlets}, {spelled} not answering."


def page(items: Sequence[NewsItem], index: int, now: datetime) -> Page | None:
    """The story at ``index`` with its words, or None when there is nothing to print."""
    if not items:
        return None
    item = items[index % len(items)]
    scene = scene_for(item)
    return Page(
        item=item,
        kicker=kicker(item.column),
        counter=counter(index % len(items), len(items)),
        byline=byline(item, now),
        read=read_label(item),
        persona=persona_of(item),
        scene=scene,
        caption=caption_for(scene),
    )
