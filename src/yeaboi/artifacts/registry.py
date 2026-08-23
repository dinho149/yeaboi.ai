"""What each editable artifact is, and which parts of it a reader may correct.

One row per artifact kind. A row says four things:

* how to rebuild the frozen dataclass from an ``asdict`` tree (the *reconstructor*),
* which lists are addressed by which natural key (``list_keys``),
* which fields are editable at all, and how long each may be (``fields``),
* and what to call the thing in a UI (``label``, ``mode``).

Why a registry rather than methods on the artifacts
---------------------------------------------------

The artifacts live in :mod:`yeaboi.agent.state` and are deliberately dumb: frozen
dataclasses with defaulted fields and no behaviour, so that a stored report from
six months ago still deserialises. Hanging an edit policy off them would put
browser-facing security decisions in the one module every other module imports.
Keeping it here also means the *deny* decisions are all readable in one screen,
which is the property that matters — see :func:`editable_field`.

This module is also the single home for the ``dict -> dataclass`` rebuilders.
:mod:`yeaboi.anonymize.apply` used to keep its own copy of that mapping, which
had drifted: it had no entry for any performance artifact, so masking a 1:1 prep
silently returned it unmasked. There is now one registry and both callers read it.

# See docs: "Guardrails" — output guardrails and untrusted browser input
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Field kinds and caps
# ---------------------------------------------------------------------------

FIELD_LINE = "line"
"""A single-line string — a headline, a title, a name."""

FIELD_TEXT = "text"
"""A multi-line string — a summary, a rationale, a block of prose."""

FIELD_ITEMS = "items"
"""A list of plain strings — bullets. Supports set / append / remove."""

FIELD_KINDS = (FIELD_LINE, FIELD_TEXT, FIELD_ITEMS)

MAX_LINE = 300
MAX_TEXT = 2000
MAX_ITEM = 500
"""Matches ``retro/board._MAX_TEXT``. A bullet someone types in a browser is a
bullet either way, and two different limits for the same gesture is a bug
waiting to be reported as one."""

MAX_ITEMS = 50
"""How many entries one list may grow to. Not a design limit — a bound on what
an untrusted joiner can append before the document stops being readable."""


@dataclass(frozen=True)
class FieldSpec:
    """One editable field, addressed by its chain of field names.

    ``chain`` is the path with every selector stripped: ``member_updates[name=Ada].summary``
    and ``member_updates[name=Grace].summary`` are both ``("member_updates", "summary")``.
    That is deliberate — the allowlist is about *which field*, never about which
    row, so a new member is editable the moment they appear without anyone
    touching this file.
    """

    chain: tuple[str, ...]
    kind: str
    label: str
    max_len: int = 0
    max_items: int = MAX_ITEMS

    def limit(self) -> int:
        """Longest accepted value, defaulted from the kind when not overridden."""
        if self.max_len:
            return self.max_len
        return {FIELD_LINE: MAX_LINE, FIELD_TEXT: MAX_TEXT, FIELD_ITEMS: MAX_ITEM}[self.kind]


def _line(chain: str, label: str, **kw) -> FieldSpec:
    return FieldSpec(chain=tuple(chain.split(".")), kind=FIELD_LINE, label=label, **kw)


def _text(chain: str, label: str, **kw) -> FieldSpec:
    return FieldSpec(chain=tuple(chain.split(".")), kind=FIELD_TEXT, label=label, **kw)


def _items(chain: str, label: str, **kw) -> FieldSpec:
    return FieldSpec(chain=tuple(chain.split(".")), kind=FIELD_ITEMS, label=label, **kw)


# ---------------------------------------------------------------------------
# Artifact rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactSpec:
    """Everything the edit machinery needs to know about one artifact kind."""

    kind: str
    dataclass_name: str
    label: str
    mode: str
    fields: tuple[FieldSpec, ...] = ()
    list_keys: Mapping[str, str] = field(default_factory=dict)
    annotatable: bool = True
    note: str = ""
    """Why this row is shaped the way it is, when that is not obvious. Shown in
    no UI — it is here so the next reader does not have to reconstruct the
    reasoning from the absence of a field."""

    def reconstruct(self, data: dict) -> Any:
        """Rebuild the frozen artifact from an ``asdict`` tree."""
        return _reconstructor(self.dataclass_name)(data)

    def field_for(self, chain: tuple[str, ...]) -> FieldSpec | None:
        """Return the spec for a field chain, or None when it is not editable."""
        for spec in self.fields:
            if spec.chain == chain:
                return spec
        return None


# The evidence and link fields are absent from every row on purpose, and the
# absence is asserted by a test rather than trusted: `*_links`, `*_evidence` and
# `images` are the fields that carry URLs, and a URL a reader can rewrite is a
# phishing edit wearing the report's own chrome. Dates, ids and computed numbers
# are absent for a duller reason — correcting prose is the point, and a
# hand-edited confidence percentage would make every trend chart a lie.

_STANDUP = ArtifactSpec(
    kind="standup",
    dataclass_name="StandupReport",
    label="Daily Standup",
    mode="standup",
    list_keys={"member_updates": "name"},
    fields=(
        _text("team_summary", "team summary", max_len=4000),
        _text("confidence_rationale", "confidence rationale"),
        _text("member_updates.summary", "summary"),
        _text("member_updates.blockers", "blockers"),
        _text("member_updates.progress_note", "progress note"),
        _text("member_updates.outlook", "outlook"),
        _text("member_updates.ticketing_summary", "ticket work"),
        _text("member_updates.code_summary", "code work"),
        _text("member_updates.documentation_summary", "documentation work"),
    ),
)

_REPORTING = ArtifactSpec(
    kind="reporting",
    dataclass_name="DeliveryReport",
    label="Delivery Report",
    mode="reporting",
    list_keys={"delivered_items": "key"},
    fields=(
        _line("headline", "headline"),
        _text("executive_summary", "executive summary", max_len=4000),
        _items("highlights", "highlight"),
        _line("delivered_items.title", "item title"),
    ),
    note=(
        "`themes` is not editable. It is a tuple of (title, outcomes) pairs, so "
        "after asdict it is a list of two-element lists with no field names for "
        "the path grammar to address. Making it editable means giving it a real "
        "dataclass first, which is a state change with its own backward-compat "
        "story; highlights carries most of the same weight in the meantime."
    ),
)

_RETRO = ArtifactSpec(
    kind="retro",
    dataclass_name="RetroReport",
    label="Retrospective",
    mode="retro",
    # Both lists are RetroCard, so both are keyed by the card id. Keying the
    # carried items by their `text` would have been the obvious reading of the
    # payload and is exactly wrong: the text is the editable field, so the first
    # correction would move the key out from under every later path.
    list_keys={"cards": "id", "carried_action_items": "id"},
    fields=(
        _text("cards.text", "card"),
        _text("carried_action_items.text", "carried action item"),
    ),
)

_ROADMAP = ArtifactSpec(
    kind="roadmap",
    dataclass_name="RoadmapAnalysis",
    label="Roadmap",
    mode="roadmap",
    list_keys={"projects": "name"},
    fields=(
        _text("summary", "roadmap summary"),
        _text("projects.description", "project description"),
        _text("projects.rationale", "why now"),
        _line("projects.quarter", "quarter"),
        _items("projects.themes", "theme"),
    ),
)

_PROFILE = ArtifactSpec(
    kind="analysis",
    dataclass_name="TeamProfile",
    label="Team Profile",
    mode="analysis",
    annotatable=True,
    fields=(),
    note=(
        "Annotations only, no field edits. Every number on a team profile is "
        "computed from tracker history, and a hand-corrected percentile is not a "
        "correction — it is a fabrication that later runs will silently "
        "contradict. What a reader actually wants here is to say *why* a number "
        "looks wrong, which is what a note is for."
    ),
)

_PERF_PREP = ArtifactSpec(
    kind="performance_prep",
    dataclass_name="OneOnOnePrep",
    label="1:1 Prep",
    mode="performance",
    fields=(
        _text("activity_summary", "sprint work"),
        _items("talking_points", "talking point"),
        _items("feedback", "feedback item"),
        _items("goals", "goal"),
        _items("gaps", "gap"),
        _items("improvements", "improvement"),
        _items("carried_action_items", "carried action item"),
    ),
)

_PERF_RECORD = ArtifactSpec(
    kind="performance_completion",
    dataclass_name="OneOnOneRecord",
    label="1:1 Summary",
    mode="performance",
    fields=(
        _line("email_subject", "email subject"),
        _text("email_summary", "summary email", max_len=4000),
        _items("action_items", "action item"),
        _items("highlights", "highlight"),
    ),
    note=(
        "`transcript` is not editable and is not shared. It is the raw recording "
        "of a private conversation; the artifact people share is the summary."
    ),
)

_PERF_REVIEW = ArtifactSpec(
    kind="performance_review",
    dataclass_name="SixMonthReview",
    label="6-Month Review",
    mode="performance",
    fields=(
        _text("overall", "overall"),
        _items("strengths", "strength"),
        _items("achievements", "achievement"),
        _items("areas_for_improvement", "area for improvement"),
        _items("goals", "goal"),
    ),
)

ARTIFACTS: dict[str, ArtifactSpec] = {
    spec.kind: spec
    for spec in (_STANDUP, _REPORTING, _RETRO, _ROADMAP, _PROFILE, _PERF_PREP, _PERF_RECORD, _PERF_REVIEW)
}


def spec_for(kind: str) -> ArtifactSpec | None:
    """Return the row for an artifact kind, or None when it is not editable."""
    return ARTIFACTS.get(kind)


def spec_for_artifact(artifact: Any) -> ArtifactSpec | None:
    """Return the row matching an artifact instance, by dataclass name."""
    name = type(artifact).__name__
    for spec in ARTIFACTS.values():
        if spec.dataclass_name == name:
            return spec
    return None


def editable_field(kind: str, chain: tuple[str, ...]) -> FieldSpec | None:
    """Return the spec for a field chain on an artifact kind, or None.

    None is the only answer that matters to a caller handling an untrusted
    request: it means "do not write here", whether because the artifact is
    unknown, the field is absent, or the field exists and is deliberately not
    editable. Those are three different reasons and one decision.
    """
    spec = ARTIFACTS.get(kind)
    return spec.field_for(chain) if spec else None


# ---------------------------------------------------------------------------
# Reconstructors
# ---------------------------------------------------------------------------

# Imported lazily inside the function: several of these live in mode stores that
# import back into agent.state, and this module is imported by anonymize, which
# must not pull six stores at import time.
_RECONSTRUCTORS: dict[str, tuple[str, str]] = {
    "StandupReport": ("yeaboi.standup.store", "_dict_to_standup_report"),
    "RetroReport": ("yeaboi.retro.store", "_dict_to_retro_report"),
    "RoadmapAnalysis": ("yeaboi.roadmap.store", "_dict_to_analysis"),
    "TeamProfile": ("yeaboi.team_profile", "_dict_to_profile"),
    "DeliveryReport": ("yeaboi.reporting.store", "_dict_to_report"),
    "PokerReport": ("yeaboi.poker.store", "_dict_to_poker_report"),
    "OneOnOnePrep": ("yeaboi.performance.store", "_dict_to_prep"),
    "OneOnOneRecord": ("yeaboi.performance.store", "_dict_to_record"),
    "SixMonthReview": ("yeaboi.performance.store", "_dict_to_review"),
    "PerformanceNote": ("yeaboi.performance.store", "_dict_to_note"),
}


def _reconstructor(name: str) -> Callable[[dict], Any]:
    module_name, attr = _RECONSTRUCTORS[name]
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)


def reconstructor_for(cls: type) -> Callable[[dict], Any] | None:
    """Return the ``dict -> dataclass`` rebuilder for an artifact class, or None.

    Looked up by class *name* so the imports can stay lazy. None for an unknown
    type, which callers treat as "leave it alone" rather than raising — a new
    mode should not crash the anonymize path before it is wired in here.

    Note this covers more than :data:`ARTIFACTS`: a poker report is
    reconstructable (anonymize masks one) without being editable (it is not a
    shared document). Reconstruction and editability are different questions.
    """
    if cls.__name__ not in _RECONSTRUCTORS:
        return None
    return _reconstructor(cls.__name__)
