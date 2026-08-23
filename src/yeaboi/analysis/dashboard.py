"""The team-analysis result cards — which ones a run earns, and their titles.

Surface-neutral on purpose: the TUI page and the desktop dashboard must agree
on the card vocabulary or the two drift into different products. Presentation
stays with each surface; the numbers each card draws live on the ``TeamProfile``
the run produced.

Three components run independently, so the visible set is composed rather than
fixed: the delivery cards appear iff a tracker profile exists, and the two
global cards (Code, Docs) do NOT depend on the active tracker, so they stay put
when the delivery toggle switches.
"""

from __future__ import annotations

#: Card key → title, in the order the cards are shown.
CARD_TITLES: dict[str, str] = {
    "velocity": "Velocity & Sprints",
    "team": "Team Members",
    "estimation": "Estimation & Points",
    "workflow": "Workflow & DoD",
    "writing": "Writing Style",
    "trends": "Trends & Repos",
    "recommendations": "Recommendations",
    "ai-adoption": "AI Usage",
    "code-health": "Code Health",
    "documentation": "Documentation",
    "insights": "Team Insights",
}

CARD_ORDER: tuple[str, ...] = tuple(CARD_TITLES)

#: The two global scan cards plus the coaching card — everything else is
#: per-delivery-tracker.
GLOBAL_CARDS: tuple[str, ...] = ("ai-adoption", "code-health", "documentation")

DELIVERY_CARD_ORDER: tuple[str, ...] = tuple(k for k in CARD_ORDER if k not in GLOBAL_CARDS)

#: Result areas a run can select independently, and the card each unlocks.
FEATURE_CARDS: dict[str, str] = {
    "code_health": "code-health",
    "ai_footprint": "ai-adoption",
    "documentation": "documentation",
}


def visible_card_order(
    profile,
    has_code: bool,
    has_docs: bool,
    *,
    has_code_health: bool = False,
    analysis_features: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Which cards to show, composing delivery cards with the global ones.

    Delivery cards (velocity/contributors/… + insights) appear iff there is a
    delivery ``profile`` for the active tracker; ``ai-adoption`` iff the global
    Code scan ran; ``documentation`` iff the global Docs scan ran. The overview
    and the detail navigation share this, so a selection index and the rendered
    card list can never drift apart.
    """
    order: list[str] = []
    if profile is not None:
        order.extend(k for k in DELIVERY_CARD_ORDER if k != "insights")
    features = set(analysis_features or ())
    explicit = analysis_features is not None
    # code-health first: it's deterministic, so it sits with the regular cards,
    # ABOVE the AI-powered group the LLM-backed cards render under.
    if has_code_health and (not explicit or "code_health" in features):
        order.append("code-health")
    if has_code and (not explicit or "ai_footprint" in features):
        order.append("ai-adoption")
    if has_docs and (not explicit or "documentation" in features):
        order.append("documentation")
    if profile is not None:
        order.append("insights")
    return tuple(order) or ("ai-adoption",)  # never empty — always render something


def component_presence(
    profile,
    *,
    code_signal=None,
    doc_signal=None,
    code_examples: dict | None = None,
    doc_examples: dict | None = None,
    examples: dict | None = None,
    analysis_features: list[str] | tuple[str, ...] | None = None,
) -> dict[str, bool]:
    """Did the Code / Code-health / Docs scans produce anything to show?

    A fresh run passes the top-level signals; a stored profile has none, so the
    same answers come off the profile, where the global scan was persisted.
    """
    prof_ai = getattr(profile, "ai_adoption", None)
    prof_doc = getattr(profile, "doc_quality", None)
    return {
        "code": code_signal is not None or bool(prof_ai and (prof_ai.scanned_commits + prof_ai.scanned_prs) > 0),
        "code_health": bool(
            (code_examples or {}).get("repository_health")
            or (examples or {}).get("ai_adoption", {}).get("repository_health")
            or (code_examples is not None and "code_health" in set(analysis_features or ()))
        ),
        "docs": doc_signal is not None or bool(prof_doc and prof_doc.pages_scanned > 0),
    }


def cards(profile, **kw) -> list[dict]:
    """The visible cards as ``[{key, title}]`` — the desktop's card list.

    Takes the same keywords as :func:`component_presence`.
    """
    features = kw.get("analysis_features")
    present = component_presence(profile, **kw)
    order = visible_card_order(
        profile,
        present["code"],
        present["docs"],
        has_code_health=present["code_health"],
        analysis_features=features,
    )
    return [{"key": key, "title": CARD_TITLES[key]} for key in order]
