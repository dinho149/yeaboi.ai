"""Product Requirements Document exporter for planning mode.

# See docs: "Prompt Construction" — ARC framework
# See docs: "Scrum Standards" — story format, Definition of Done
#
# Builds a stakeholder-facing PRD from the plan artifacts. The document is
# mostly DETERMINISTIC assembly (MVP scope from features, user stories
# verbatim, architecture from the analyzer's decision, implementation phases
# from the sprints); ONE LLM call writes the prose sections (executive
# summary, mission, target users, success criteria, future considerations,
# risks & mitigations), copying the poker-engine single-call pattern:
# is_llm_configured gate → invoke_json → per-key validation → classified
# fallbacks. A fallback run still emits the FULL document — deterministic
# sections plus honest placeholders — with llm_mode "fallback" and a warning,
# per the headless-engine convention.
#
# Same *_exporter naming and builder/writer split as html_exporter.py and
# repl/_io.py's markdown builder. Publishing to Notion/Confluence reuses
# export_targets.publish_markdown unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from yeaboi.agent.state import QuestionnaireState, resolve_dod_items

logger = logging.getLogger(__name__)

# Keywords that route constraints/integrations into the Security section.
_SECURITY_WORDS = ("auth", "security", "secret", "credential", "encrypt", "gdpr", "compliance", "sso", "oauth", "token")

# Keywords that make the conditional API Specification section appear.
_API_WORDS = ("api", "endpoint", "rest", "graphql", "webhook")


@dataclass(frozen=True)
class PrdResult:
    """A built PRD: the document plus how it was produced."""

    markdown: str = ""
    llm_mode: str = "fallback"  # "llm" | "fallback"
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Context digest + prose generation (the one LLM call)
# ---------------------------------------------------------------------------


def _prd_context_digest(graph_state: dict) -> str:
    """Compact markdown digest of the plan — the grounding for the prose call."""
    lines: list[str] = []
    analysis = graph_state.get("project_analysis")
    if analysis is not None:
        lines.append(f"Project: {analysis.project_name} ({analysis.project_type})")
        lines.append(f"Description: {analysis.project_description}")
        if analysis.target_state:
            lines.append(f"Target state: {analysis.target_state}")
        for label, items in (
            ("Goals", analysis.goals),
            ("End users", analysis.end_users),
            ("Tech stack", analysis.tech_stack),
            ("Constraints", analysis.constraints),
            ("Risks", analysis.risks),
            ("Out of scope", analysis.out_of_scope),
            ("Assumptions", analysis.assumptions),
        ):
            if items:
                lines.append(f"{label}: {'; '.join(items)}")
        arch = getattr(analysis, "architecture", None)
        if arch is not None and arch.options:
            lines.append(f"Architecture: {arch.chosen} (confidence {arch.confidence})")

    features = graph_state.get("features") or []
    if features:
        lines.append("Features:")
        lines.extend(f"- {f.title}: {f.description}" for f in features)

    stories = graph_state.get("stories") or []
    if stories:
        lines.append("Stories:")
        lines.extend(f"- {s.title or s.goal} ({s.story_points} pts)" for s in stories)

    sprints = graph_state.get("sprints") or []
    if sprints:
        lines.append("Sprints:")
        lines.extend(f"- {sp.name}: {sp.goal}" for sp in sprints)

    qs = graph_state.get("questionnaire")
    if isinstance(qs, QuestionnaireState):
        for q_num, label in ((5, "Deadline"), (6, "Team size")):
            answer = qs.answers.get(q_num)
            if answer:
                lines.append(f"{label}: {answer}")

    prior_art = graph_state.get("prior_art") or ()
    if prior_art:
        lines.append("Builds on existing repositories: " + ", ".join(ref.name for ref in prior_art))

    return "\n".join(lines)


def _fallback_prose(graph_state: dict) -> dict:
    """Deterministic placeholder prose — honest skeletons, never invented facts."""
    analysis = graph_state.get("project_analysis")
    goals = list(getattr(analysis, "goals", ()) or ())
    out_of_scope = list(getattr(analysis, "out_of_scope", ()) or ())
    return {
        "executive_summary": (
            f"{getattr(analysis, 'project_description', '') or 'Project description unavailable.'} "
            f"Target state: {getattr(analysis, 'target_state', '') or 'not specified.'}"
        ),
        "mission": goals[0] if goals else "Deliver the planned scope.",
        "target_users": [{"persona": u, "description": ""} for u in getattr(analysis, "end_users", ()) or ()],
        "success_criteria": ([getattr(analysis, "target_state", "")] if getattr(analysis, "target_state", "") else [])
        + goals,
        "future_considerations": [f"Revisit later: {item}" for item in out_of_scope],
        "risks_mitigations": [
            {"risk": r, "mitigation": "To be defined."} for r in getattr(analysis, "risks", ()) or ()
        ],
    }


def _generate_prose_sections(graph_state: dict) -> tuple[dict, str, list[str]]:
    """The single LLM call. Returns (sections, llm_mode, warnings) — never raises.

    A missing/empty key in the LLM's reply falls back to that section's
    deterministic text with a warning; a failed/unconfigured call falls back
    wholesale, classified like every other yeaboi engine.
    """
    from yeaboi.prompts.prd import PRD_PROSE_KEYS, get_prd_prose_prompt

    fallback = _fallback_prose(graph_state)

    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("prd: LLM not configured (%s) — deterministic prose", why)
        return fallback, "fallback", [_fallback_warning(f"AI unavailable ({why})")]

    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    analysis = graph_state.get("project_analysis")
    prompt = get_prd_prose_prompt(
        _prd_context_digest(graph_state),
        has_architecture=bool(getattr(getattr(analysis, "architecture", None), "options", ())),
    )
    try:
        response = invoke_json(prompt, temperature=0.3)
        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        parsed = json.loads(text.strip())
        if not isinstance(parsed, dict):
            raise ValueError("PRD prose reply was not a JSON object")
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("prd: LLM auth/billing error: %s", exc)
            return fallback, "fallback", [_fallback_warning("AI unavailable (API key/billing)")]
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("prd: local Ollama failure: %s", exc)
            return fallback, "fallback", [local_hint]
        logger.warning("prd: LLM request failed, deterministic prose: %s", exc)
        return fallback, "fallback", [_fallback_warning("AI request failed (see logs)")]

    warnings: list[str] = []
    sections: dict = {}
    for key in PRD_PROSE_KEYS:
        value = parsed.get(key)
        if value:
            sections[key] = value
        else:
            sections[key] = fallback[key]
            warnings.append(f"PRD section '{key}' missing from the AI reply — deterministic placeholder used.")
    return sections, "llm", warnings


def _fallback_warning(reason: str) -> str:
    return (
        f"{reason} — Executive Summary, Mission, Target Users, Success Criteria, "
        "Future Considerations and Risks are deterministic placeholders."
    )


# ---------------------------------------------------------------------------
# Deterministic section builders
# ---------------------------------------------------------------------------


def _as_lines(value: object, bullet: str = "- ") -> list[str]:
    """Render a prose value (string or list of strings/dicts) as markdown lines."""
    if isinstance(value, str):
        return [value]
    lines: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            parts = [str(v) for v in item.values() if v]
            if not parts:  # a dict of empty values renders as nothing, not a crash
                continue
            lines.append(f"{bullet}{parts[0] if len(parts) == 1 else parts[0] + ' — ' + '; '.join(parts[1:])}")
        elif item:
            lines.append(f"{bullet}{item}")
    return lines


def _mvp_scope(graph_state: dict) -> list[str]:
    lines = ["## MVP Scope", "", "### In Scope", ""]
    features = graph_state.get("features") or []
    stories = graph_state.get("stories") or []
    by_feature: dict[str, list] = {}
    for story in stories:
        by_feature.setdefault(story.feature_id, []).append(story)
    if features:
        for feature in features:
            f_stories = by_feature.get(feature.id, [])
            pts = sum(int(s.story_points) for s in f_stories)
            detail = f" ({len(f_stories)} stories, {pts} pts)" if f_stories else ""
            lines.append(f"- ✅ **{feature.title}**{detail} — {feature.description}")
    else:
        lines.append("- ✅ (no features recorded)")
    analysis = graph_state.get("project_analysis")
    out_of_scope = list(getattr(analysis, "out_of_scope", ()) or ())
    if out_of_scope:
        lines += ["", "### Out of Scope", ""]
        lines.extend(f"- ❌ {item}" for item in out_of_scope)
    assumptions = list(getattr(analysis, "assumptions", ()) or ())
    if assumptions:
        lines += ["", "### Assumptions", ""]
        lines.extend(f"- ⚠️ {item}" for item in assumptions)
    return lines


def _user_stories(graph_state: dict) -> list[str]:
    stories = graph_state.get("stories") or []
    if not stories:
        return []
    features = {f.id: f for f in graph_state.get("features") or []}
    lines = ["## User Stories", ""]
    by_feature: dict[str, list] = {}
    for story in stories:
        by_feature.setdefault(story.feature_id, []).append(story)
    for feature_id, group in by_feature.items():
        feature = features.get(feature_id)
        if feature:
            lines.append(f"### {feature.title}")
            lines.append("")
        for story in group:
            pri = story.priority.value if hasattr(story.priority, "value") else story.priority
            disc = story.discipline.value if hasattr(story.discipline, "value") else story.discipline
            lines.append(f"- **{story.title or story.goal}** ({story.story_points} pts, {pri}, {disc})")
            lines.append(f"  - {story.text}")
            # flat_text renders both AC shapes — a bullets team's PRD carries
            # their own criteria wording, not a forced Gherkin rewrite.
            lines.extend(f"  - AC: {ac.flat_text}" for ac in story.acceptance_criteria)
        lines.append("")
    return lines


def _architecture(graph_state: dict) -> list[str]:
    analysis = graph_state.get("project_analysis")
    arch = getattr(analysis, "architecture", None)
    if arch is None or not arch.options:
        return []
    lines = ["## Core Architecture & Patterns", ""]
    lines.append(f"**Recommended:** {arch.chosen} (confidence: {arch.confidence})")
    if arch.rationale:
        lines.append(f"\n{arch.rationale}")
    lines.append("")
    for opt in arch.options:
        marker = "✓ " if opt.name == arch.chosen else ""
        lines.append(f"- **{marker}{opt.name}** — {opt.summary}")
        if opt.pros:
            lines.append(f"  - Pros: {'; '.join(opt.pros)}")
        if opt.cons:
            lines.append(f"  - Cons: {'; '.join(opt.cons)}")
    if arch.pinned_by_constraint:
        lines.append("\n*(decision pinned by an existing constraint)*")
    return lines


def _features_table(graph_state: dict) -> list[str]:
    features = graph_state.get("features") or []
    if not features:
        return []
    lines = ["## Tools / Features", "", "| ID | Feature | Priority | Description |", "|---|---|---|---|"]
    for f in features:
        pri = f.priority.value if hasattr(f.priority, "value") else f.priority
        lines.append(f"| {f.id} | {f.title} | {pri} | {f.description} |")
    return lines


def _tech_stack(graph_state: dict) -> list[str]:
    analysis = graph_state.get("project_analysis")
    stack = list(getattr(analysis, "tech_stack", ()) or ())
    integrations = list(getattr(analysis, "integrations", ()) or ())
    if not stack and not integrations:
        return []
    lines = ["## Technology Stack", ""]
    lines.extend(f"- {item}" for item in stack)
    if integrations:
        lines += ["", "### Integrations", ""]
        lines.extend(f"- {item}" for item in integrations)
    return lines


def _security(graph_state: dict) -> list[str]:
    analysis = graph_state.get("project_analysis")
    pool = list(getattr(analysis, "constraints", ()) or ()) + list(getattr(analysis, "integrations", ()) or ())
    hits = [item for item in pool if any(w in item.lower() for w in _SECURITY_WORDS)]
    lines = ["## Security & Configuration", ""]
    if hits:
        lines.extend(f"- {item}" for item in hits)
    else:
        lines.append("*(no security-specific constraints captured during intake — confirm before build)*")
    return lines


def _api_spec(graph_state: dict) -> list[str]:
    """Conditional: only when the plan is visibly API-shaped."""
    analysis = graph_state.get("project_analysis")
    stack_text = " ".join(getattr(analysis, "tech_stack", ()) or ()).lower()
    stories = graph_state.get("stories") or []
    api_stories = [s for s in stories if any(w in f"{s.title} {s.goal}".lower() for w in _API_WORDS)]
    if not api_stories and not any(w in stack_text for w in _API_WORDS):
        return []
    lines = ["## API Specification", ""]
    if api_stories:
        lines.append("API-shaped stories to specify endpoints from:")
        lines.extend(f"- {s.title or s.goal}" for s in api_stories)
    else:
        lines.append("*(the stack is API-oriented; endpoint specifications to be drafted during the first sprint)*")
    return lines


def _implementation_phases(graph_state: dict) -> list[str]:
    sprints = graph_state.get("sprints") or []
    if not sprints:
        return []
    stories = {s.id: s for s in graph_state.get("stories") or []}
    dod = resolve_dod_items(graph_state)
    lines = ["## Implementation Phases", ""]
    for i, sprint in enumerate(sprints, 1):
        lines.append(f"### Phase {i}: {sprint.name}")
        lines.append("")
        lines.append(f"**Goal:** {sprint.goal}  |  **Capacity:** {sprint.capacity_points} pts")
        lines.append("")
        lines.append("Deliverables:")
        for sid in sprint.story_ids:
            story = stories.get(sid)
            if story:
                lines.append(f"- ✅ {story.title or story.goal}")
        lines.append(f"\nValidation: all acceptance criteria pass and the Definition of Done holds ({', '.join(dod)}).")
        lines.append("")
    return lines


def _appendix(graph_state: dict, llm_mode: str) -> list[str]:
    lines = ["## Appendix", ""]
    weeks = graph_state.get("sprint_length_weeks")
    velocity = graph_state.get("net_velocity_per_sprint") or graph_state.get("velocity_per_sprint")
    team = graph_state.get("team_size")
    cadence = []
    if weeks:
        cadence.append(f"{weeks}-week sprints")
    if velocity:
        cadence.append(f"{velocity} pts/sprint")
    if team:
        cadence.append(f"{team} engineer(s)")
    if cadence:
        lines.append(f"- Cadence: {', '.join(cadence)}")
    prior_art = graph_state.get("prior_art") or ()
    if prior_art:
        lines.append(
            "- Prior art: " + ", ".join(f"[{ref.name}]({ref.url})" if ref.url else ref.name for ref in prior_art)
        )
    start = graph_state.get("sprint_start_date")
    if start:
        lines.append(f"- Planned start: {start}")
    lines.append(f"- Generated by yeaboi planning mode (prose: {'AI' if llm_mode == 'llm' else 'deterministic'}).")
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_prd_markdown(graph_state: dict) -> PrdResult:
    """Build the full PRD markdown from a plan's graph state. Never raises."""
    analysis = graph_state.get("project_analysis")
    logger.info(
        "prd: building — features=%d stories=%d sprints=%d",
        len(graph_state.get("features") or []),
        len(graph_state.get("stories") or []),
        len(graph_state.get("sprints") or []),
    )
    prose, llm_mode, warnings = _generate_prose_sections(graph_state)

    name = getattr(analysis, "project_name", "") or graph_state.get("project_name") or "Untitled Project"
    parts: list[str] = [f"# PRD — {name}", ""]

    def _section(title: str, value: object) -> None:
        rendered = _as_lines(value)
        if rendered:
            parts.append(f"## {title}")
            parts.append("")
            parts.extend(rendered)
            parts.append("")

    _section("Executive Summary", prose["executive_summary"])
    _section("Mission", prose["mission"])
    _section("Target Users", prose["target_users"])
    for block in (
        _mvp_scope(graph_state),
        _user_stories(graph_state),
        _architecture(graph_state),
        _features_table(graph_state),
        _tech_stack(graph_state),
        _security(graph_state),
        _api_spec(graph_state),
    ):
        if block:
            parts.extend(block)
            parts.append("")
    _section(
        "Success Criteria", [f"✅ {c}" if isinstance(c, str) else c for c in _ensure_list(prose["success_criteria"])]
    )
    phase_block = _implementation_phases(graph_state)
    if phase_block:
        parts.extend(phase_block)
        parts.append("")
    _section("Future Considerations", prose["future_considerations"])
    _section("Risks & Mitigations", prose["risks_mitigations"])
    parts.extend(_appendix(graph_state, llm_mode))
    parts.append("")

    markdown = "\n".join(parts)
    logger.info("prd: built — %d chars, llm_mode=%s, warnings=%d", len(markdown), llm_mode, len(warnings))
    return PrdResult(markdown=markdown, llm_mode=llm_mode, warnings=tuple(warnings))


def _ensure_list(value: object) -> list:
    return value if isinstance(value, list) else [value]


def export_prd_markdown(graph_state: dict, path: Path | None = None, result: PrdResult | None = None) -> Path:
    """Write the PRD to a markdown file and return its path.

    Pass a pre-built ``result`` to avoid a second LLM call when the caller
    already has one (e.g. to surface its warnings separately).
    """
    from yeaboi.fs_policy import resolve_and_check

    output_path = resolve_and_check(path or Path("prd.md"), mode="write", context="PRD markdown export")
    built = result or build_prd_markdown(graph_state)
    # Explicit encoding: the PRD always carries non-ASCII (✅/❌/⚠️ scope
    # markers), and a cp1252 default would raise after the LLM call was paid.
    output_path.write_text(built.markdown, encoding="utf-8")
    logger.info("prd: exported to %s (llm_mode=%s)", output_path, built.llm_mode)
    return output_path
