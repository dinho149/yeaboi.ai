"""What a team-analysis setup wizard offers, and what a finished one asks for.

The wizard's *steps* stay with each surface (arrow keys in the terminal, a form
on the desktop); which steps apply, what they may offer, and how the answers
become a run payload live here, so the two surfaces cannot start different
runs. Deliberately not in ``engine.py``: the engine glob would force every
public name here into the parity registry as a capability of its own.

The three probes answer "what is configured", never "what shall we scan" —
selection is the wizard's job and the run's payload its result.
"""

from __future__ import annotations

#: Independently selectable result areas → the label every surface shows.
FEATURES: dict[str, str] = {
    "delivery": "Delivery",
    "ai_footprint": "AI footprint",
    "code_health": "Code health",
    "documentation": "Documentation",
}

#: Features that read code, and so need a Code component and a change window.
CODE_FEATURES: frozenset[str] = frozenset({"ai_footprint", "code_health"})

#: The steps a wizard walks, in order.
STEPS: tuple[str, ...] = (
    "features",
    "sources",
    "github_owners",
    "azdo_projects",
    "depth",
    "model",
    "window",
    "members",
    "review",
)

#: Steps every run asks, whatever was selected.
ALWAYS_APPLICABLE: frozenset[str] = frozenset({"features", "sources", "review"})

DEPTHS: tuple[str, ...] = ("quick", "deep")
DEFAULT_DEPTH = "deep"
DEFAULT_WINDOW_DAYS = 120
WINDOW_PRESETS: tuple[int, ...] = (30, 60, 120, 180, 365)


def available_trackers() -> list[str]:
    """Which trackers are configured (creds present). Ordered jira-first — the
    same precedence as ``_detect_source`` — so 'both' output is deterministic."""
    available: list[str] = []
    try:
        from yeaboi.config import get_jira_base_url, get_jira_token

        if get_jira_base_url() and get_jira_token():
            available.append("jira")
    except Exception:
        pass
    try:
        from yeaboi.config import get_azure_devops_org_url, get_azure_devops_token

        if get_azure_devops_org_url() and get_azure_devops_token():
            available.append("azdevops")
    except Exception:
        pass
    return available


def scannable_code_sources() -> list[str]:
    """Which remote code hosts are configured (GitHub, Azure Repos). Used to build
    the picker's Code row and to default ``components=None``."""
    out: list[str] = []
    try:
        from yeaboi.config import get_github_token, get_team_analysis_github_owners

        if get_team_analysis_github_owners() and get_github_token():
            out.append("github")
    except Exception:
        pass
    try:
        from yeaboi.config import get_azure_devops_token, get_team_analysis_azdo_projects

        if get_team_analysis_azdo_projects() and get_azure_devops_token():
            out.append("azdo")
    except Exception:
        pass
    return out


def offerable_code_sources() -> list[str]:
    """Which code hosts the setup wizard may OFFER, as opposed to scan unattended.

    Deliberately distinct from :func:`scannable_code_sources`, which answers
    "scannable with zero further input" and drives the headless component default
    (``_default_components``). GitHub needs only a token here because the wizard
    discovers the owners itself (``_run_code_scope_select``) — whereas a headless
    run has nobody to ask, so it still requires configured owners. Azure is the
    same in both: its project list falls back to ``AZURE_DEVOPS_PROJECT``."""
    out: list[str] = []
    try:
        from yeaboi.config import get_github_token

        if get_github_token():
            out.append("github")
    except Exception:
        pass
    try:
        from yeaboi.config import get_azure_devops_token, get_team_analysis_azdo_projects

        if get_team_analysis_azdo_projects() and get_azure_devops_token():
            out.append("azdo")
    except Exception:
        pass
    return out


def available_doc_sources() -> list[str]:
    """Which doc platforms are configured (Confluence, Notion). Used to build the
    picker's Docs row."""
    out: list[str] = []
    try:
        from yeaboi.config import get_confluence_base_url, get_confluence_token

        if get_confluence_token() and get_confluence_base_url():
            out.append("confluence")
    except Exception:
        pass
    try:
        from yeaboi.config import get_notion_token

        if get_notion_token():
            out.append("notion")
    except Exception:
        pass
    return out


def available_grid() -> dict[str, list[str]]:
    """Component → the sub-sources this machine is configured to offer."""
    return {
        "delivery": available_trackers(),
        "code": offerable_code_sources(),
        "docs": available_doc_sources(),
    }


def filtered_grid(grid: dict[str, list[str]], features) -> dict[str, list[str]]:
    """The grid narrowed to the components the selected features actually read."""
    chosen = set(features or ())
    return {
        "delivery": grid.get("delivery", []) if "delivery" in chosen else [],
        "code": grid.get("code", []) if chosen & CODE_FEATURES else [],
        "docs": grid.get("docs", []) if "documentation" in chosen else [],
    }


def depth_applies(features) -> bool:
    """Depth only means something where an LLM has ticket text to read."""
    return bool(set(features or ()) & {"delivery", "ai_footprint"})


def effective_depth(depth: str, features) -> str:
    """A stale ``deep`` can never leak into a run with nothing to read."""
    return depth if depth_applies(features) else "quick"


def step_applies(
    step: str, *, features, components=None, depth: str = DEFAULT_DEPTH, model_offered: bool = False
) -> bool:
    """Does ``step`` apply, given what has been selected so far?

    A choice made for a step that later becomes inapplicable stays in the
    wizard's own state (so re-enabling a feature restores it) — it is here that
    it is kept out of the run.
    """
    chosen = set(features or ())
    comps = components or {}
    if step in ALWAYS_APPLICABLE:
        return True
    if step in ("github_owners", "azdo_projects"):
        host = "github" if step == "github_owners" else "azdo"
        return bool(chosen & CODE_FEATURES) and host in (comps.get("code") or [])
    if step == "depth":
        return depth_applies(features)
    if step == "model":
        return effective_depth(depth, features) == "deep" and model_offered
    if step == "window":
        return bool(chosen & (CODE_FEATURES | {"documentation"}))
    if step == "members":
        return bool(chosen & (CODE_FEATURES | {"delivery"}))
    return False


def run_config(state: dict, *, roster_fallback, model_offered: bool = False) -> dict:
    """Turn a completed wizard's answers into the run payload.

    Every host's scope is gated on its OWN applicability, so de-selecting a code
    host coerces its stale picks out of the payload — the same discipline that
    keeps a stale ``deep`` out of a docs-only run.
    """
    features = state.get("features")
    comps = state.get("components") or {}
    depth = state.get("depth") or DEFAULT_DEPTH

    def applies(step: str) -> bool:
        return step_applies(step, features=features, components=comps, depth=depth, model_offered=model_offered)

    members = state.get("members") if applies("members") else None
    trackers = comps.get("delivery") or roster_fallback
    scope: dict[str, list[str]] = {}
    for step, host in (("github_owners", "github"), ("azdo_projects", "azdo")):
        if applies(step) and state.get(step):
            scope[host] = state[step]
    return {
        "features": features,
        "components": comps,
        "analysis_scope": scope,
        "depth": effective_depth(depth, features),
        "model": state.get("model") if applies("model") else None,
        "window_days": state.get("window_days", DEFAULT_WINDOW_DAYS) if applies("window") else DEFAULT_WINDOW_DAYS,
        "members": members,
        "members_map": {tracker: members for tracker in trackers} if members else None,
    }
