"""The chips Niko offers before you've typed anything.

Keyed by **capability**, not by route, and the route→capability mapping is read
from the committed desktop manifest. That is what stops the chips going stale:
rename a route and the mapping follows it; drop a capability and
``TestNikoSuggestions`` fails rather than the panel quietly offering a screen
that no longer exists.

Resolution order matches the surface's own specificity — exact route, then the
capability that owns it, then the section (``/team`` vs ``/agents``), then a
default set. A screen with nothing to say falls back rather than showing
nothing: an empty chip list reads as a broken panel.

``icon`` values are the strings the renderer's ICON_MAP already understands;
an unmapped one degrades to a compass rather than breaking the row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _chip(label: str, prompt: str, icon: str) -> dict:
    return {"label": label, "prompt": prompt, "icon": icon}


#: What to ask on a screen, keyed by the CAPABILITIES row that owns it.
BY_CAPABILITY: dict[str, list[dict]] = {
    "planning": [
        _chip("What have I planned?", "List my saved planning sessions and how far each one got.", "layers"),
        _chip("Where did this plan come from?", "Trace the decisions behind my most recent plan.", "compass"),
    ],
    "roadmap": [
        _chip("What is roadmap intake?", "Explain what Roadmap intake does and when I should use it.", "info"),
    ],
    "team-analysis": [
        _chip("How is my team doing?", "Summarise my team's delivery profile.", "trending-up"),
        _chip("Who is on the team?", "Show me the team roster yeaboi knows about.", "users"),
    ],
    "standup": [
        _chip("What did the last standup find?", "Summarise my most recent standup run.", "calendar"),
        _chip("Am I running standups?", "Is a standup scheduled, and did it fire?", "bell"),
    ],
    "retro-board": [
        _chip("What came out of my retros?", "Summarise the actions from my recent retros.", "layout"),
    ],
    "scrum-poker": [
        _chip("What did we estimate?", "Summarise my recent planning-poker sessions.", "layers"),
    ],
    "performance": [
        _chip("Who can I prep for?", "Which engineers can I prepare a 1:1 or review for?", "users"),
    ],
    "reporting": [
        _chip("What have I reported?", "List my recent delivery reports.", "bar-chart"),
    ],
    "ship": [
        _chip("Is anything waiting on me?", "Is a Ship run sitting at the approval gate?", "alert-triangle"),
        _chip("How have Ship runs gone?", "Summarise my recent Ship runs.", "play"),
    ],
    "agent-usage": [
        _chip("What did my agents cost?", "What have my AI coding agents been costing me?", "bar-chart"),
        _chip("Where did the spend go?", "Which models and projects dominated my agent spend?", "trending-up"),
    ],
    "agent-advisor": [
        _chip("What spend was avoidable?", "How much of my agent spend was recoverable?", "trending-up"),
    ],
    "agent-standup": [
        _chip("What did my agents ship?", "Summarise what my AI agents shipped recently.", "play"),
    ],
    "agent-security": [
        _chip("Are my agents safe?", "Summarise the security posture of my AI coding agents.", "shield-check"),
    ],
    "ceremonies": [
        _chip("What is scheduled?", "What ceremonies are scheduled, and are their jobs installed?", "calendar"),
        _chip("Did anything fail?", "Did any scheduled ceremony fail or get skipped recently?", "alert-triangle"),
    ],
    "slack-inbound": [
        _chip("What is the Slack lane?", "Explain what the inbound Slack lane does.", "info"),
    ],
    "provenance": [
        _chip("Why did we decide that?", "Show me the decisions recorded in the last 30 days.", "compass"),
        _chip("Anything conflicting?", "Are there conflicting decisions in the record?", "alert-triangle"),
    ],
    "usage": [
        _chip("What has yeaboi cost me?", "What has yeaboi's own LLM usage cost me?", "bar-chart"),
    ],
    "settings": [
        _chip("What can yeaboi do?", "Give me a tour of what yeaboi can do.", "info"),
    ],
}

#: Section fallbacks, for a screen whose capability has no chips of its own.
BY_SECTION: dict[str, list[dict]] = {
    "/team": [
        _chip("What should I do next?", "Based on my data, what should I work on next?", "compass"),
        _chip("What have I planned?", "List my saved planning sessions and how far each one got.", "layers"),
    ],
    "/solo": [
        _chip("How did my week go?", "Summarise my latest weekly review: what went well, what to change.", "calendar"),
        _chip("Am I on track?", "Am I on track against my sprint plan, based on my standups?", "compass"),
    ],
    "/agents": [
        _chip("What did my agents cost?", "What have my AI coding agents been costing me?", "bar-chart"),
        _chip("Are my agents safe?", "Summarise the security posture of my AI coding agents.", "shield-check"),
    ],
}

#: The home screen, an unknown route, and anything else with nothing to say.
DEFAULT: list[dict] = [
    _chip("What can yeaboi do?", "Give me a tour of what yeaboi can do.", "info"),
    _chip("What should I do next?", "Based on my data, what should I work on next?", "compass"),
    _chip("What did my agents cost?", "What have my AI coding agents been costing me?", "bar-chart"),
]

#: How many chips the panel shows. Three fits the empty state without scrolling.
MAX_CHIPS = 3


def route_index() -> dict[str, dict]:
    """``route -> {capability, title}``, from the committed desktop manifest."""
    from yeaboi.niko.tools import known_routes

    return {
        str(row.get("path", "")): {
            "capability": str(row.get("capability") or ""),
            "title": str(row.get("title") or ""),
        }
        for row in known_routes()
        if row.get("path")
    }


def screen_for(route: str) -> dict:
    """What the manifest says about ``route`` — longest matching prefix wins.

    Prefix rather than exact so ``/team/retro/board`` inherits the retro row
    when the deeper path is not itself registered, which is how the renderer's
    own active-state works.
    """
    index = route_index()
    if route in index:
        return index[route]
    matches = [path for path in index if path.startswith("/") and route.startswith(path + "/")]
    if matches:
        return index[max(matches, key=len)]
    return {"capability": "", "title": ""}


def for_route(route: str) -> list[dict]:
    """The chips to offer on ``route``."""
    capability = screen_for(route).get("capability", "")
    if capability and capability in BY_CAPABILITY:
        chips = BY_CAPABILITY[capability]
    else:
        section = next((key for key in BY_SECTION if route.startswith(key)), "")
        chips = BY_SECTION.get(section, DEFAULT)
    logger.info("niko suggestions: route=%s capability=%s chips=%d", route, capability, len(chips[:MAX_CHIPS]))
    return chips[:MAX_CHIPS]
