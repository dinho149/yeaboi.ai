"""Surface-parity registry — every capability must ship on every surface (or be exempted).

# See docs: "MCP Server" — the delivery surfaces

yeaboi has six delivery surfaces: the TUI, CLI flags/subcommands, the Python
engines, the MCP server, the Claude Code plugin skills, and the desktop app.
Features have a habit of landing TUI-only. This file is the enforcement: a
declarative registry of capabilities mapped to the surfaces that implement
them, plus discovery checks that FAIL when something new appears on one
surface without being registered (and therefore consciously propagated — or
consciously exempted — everywhere else).

Discovery strategy mirrors ``tests/unit/tools/test_tools_registry.py``:
AST-scan engine modules (no imports, no side effects), introspect the real
FastMCP app for the tool inventory, read ``_MODE_CARDS`` for TUI modes, and
``build_parser()`` for CLI flags. Two-way set equality everywhere it's
meaningful, so removals rot the registry as loudly as additions.

The param-parity checks are the sharpest edge: for each MCP tool that wraps an
engine, the engine's keyword surface must be exposed on the tool, hidden via a
reasoned ``HIDDEN_PARAMS`` entry, or be a universal injection seam
(``HIDDEN_ALWAYS``). A new engine param therefore breaks the build until the
MCP tool grows it too.

How to fix a failure: update ``CAPABILITIES``/``PARAM_PAIRS`` below, or record
an ``Exempt(reason)``/``HIDDEN_PARAMS`` entry — see CLAUDE.md
"REQUIRED: Surface Parity".
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import NamedTuple

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "yeaboi"
PLUGIN_SKILLS_DIR = REPO_ROOT / "claude-plugin" / "yeaboi" / "skills"

_HOW_TO = (
    "Fix: update CAPABILITIES/PARAM_PAIRS in tests/unit/test_surface_parity.py, or record an "
    "Exempt(reason)/HIDDEN_PARAMS entry — see CLAUDE.md 'REQUIRED: Surface Parity'."
)


class Exempt(NamedTuple):
    """A deliberate, reasoned absence of a capability on one surface."""

    reason: str


# ---------------------------------------------------------------------------
# The registry — one row per capability, one column per surface.
#
#   engines:   set[(module, function)] — headless pipeline entry points
#   mcp_tools: set[str]                — MCP tool names on the yeaboi-mcp server
#   tui_mode:  str                     — the _MODE_CARDS key
#   cli:       set[str]                — argparse flags / subcommands
#   skill:     str                     — claude-plugin/yeaboi/skills/<name>/
#   desktop:   set[str]                — desktop renderer routes/actions, checked
#                                        against contracts/v1/routes_manifest.json
#                                        (committed by the desktop renderer build).
#                                        "desktop: scheduled milestone" Exempts are
#                                        the rollout ledger — each milestone burns
#                                        its own down; M12 removes the last.
#
# Any column may instead hold Exempt("why this surface is deliberately absent").
# ---------------------------------------------------------------------------

CAPABILITIES: dict[str, dict] = {
    "planning": {
        "engines": {("yeaboi.agent.headless", "run_planning_pipeline")},
        "mcp_tools": {
            "plan_generate",
            "intake_questions",
            "plan_get",
            "plan_export",
            "plan_publish",
            "plan_sync",
            "plan_prior_art",
            "plan_prior_art_feedback",
        },
        "tui_mode": "project-planning",
        "cli": {
            "--non-interactive",
            "--description",
            "--output",
            "--team-size",
            "--sprint-length",
            "--quick",
            "--questionnaire",
            "--export-questionnaire",
            "--export-only",
            "--mode",
            "--prior-art",
            "--ac-format",
            "--architecture-spike",
        },
        "skill": "plan-sprint",
        "desktop": {"/humans/planning", "/humans/planning/chat", "/humans/planning/plan"},
    },
    "sessions": {
        "engines": Exempt("thin SessionStore reads — no pipeline to extract"),
        "mcp_tools": {"sessions_list", "session_get", "session_delete"},
        "tui_mode": Exempt("sessions are surfaced inside the planning-mode screens, no dedicated card"),
        "cli": {"--list-sessions", "--resume", "--clear-sessions"},
        "skill": Exempt("agents call the session tools directly — no guided workflow needed"),
        "desktop": {"/humans/planning/sessions"},
    },
    "standup": {
        "engines": {
            ("yeaboi.standup.engine", "run_standup"),
            ("yeaboi.standup.engine", "run_transcript_review"),
            ("yeaboi.standup.engine", "import_transcript"),
            ("yeaboi.standup.engine", "transcript_nudge"),
            ("yeaboi.standup.engine", "file_transcript_issues"),
        },
        "mcp_tools": {
            "standup_run",
            "standup_history",
            "standup_config_get",
            "standup_config_set",
            "standup_members",
            "standup_repositories",
            "standup_review",
            "standup_gaps",
            "standup_practice_feedback",
        },
        "tui_mode": "daily-standup",
        "cli": {
            "standup",
            "standup-review",
            "--standup-run",
            "--standup-session",
            "--standup-output",
            "--standup-interactive",
        },
        "skill": "standup",
        "desktop": {
            "/humans/standup",
            "/humans/standup/setup",
            "/humans/standup/schedule",
            "/humans/standup/review",
        },
    },
    "reporting": {
        "engines": {("yeaboi.reporting.engine", "run_delivery_report")},
        # reporting_history / reporting_export are read-only store/export wrappers
        # (no pipeline) — the saved-runs hub surfaces them; parity with retro-board.
        "mcp_tools": {"report_delivery", "reporting_history", "reporting_export"},
        "tui_mode": "reporting",
        "cli": {"report"},
        "skill": "delivery-report",
        "desktop": {"/humans/reporting", "/humans/reporting/new", "/humans/reporting/style"},
    },
    "performance": {
        "engines": {
            ("yeaboi.performance.engine", "run_one_on_one_prep"),
            ("yeaboi.performance.engine", "complete_one_on_one"),
            ("yeaboi.performance.engine", "run_six_month_review"),
        },
        "mcp_tools": {
            "perf_roster",
            "perf_one_on_one_prep",
            "perf_one_on_one_complete",
            "perf_six_month_review",
            "perf_note_add",
        },
        "tui_mode": "performance",
        "cli": {"perf"},
        "skill": "performance",
        "desktop": {"/humans/performance", "/humans/performance/engineer"},
    },
    "scrum-poker": {
        # get_poker_perspective: the one LLM call (AI take on a revealed vote
        # spread); the live voting board itself is a real-time server the TUI
        # hosts and tunnels — like retro, it can't be a one-shot pipeline.
        "engines": {("yeaboi.poker.engine", "get_poker_perspective")},
        "mcp_tools": {"poker_history", "poker_export"},
        "tui_mode": "poker",
        "cli": {"poker"},  # history read-back + export; the live voting board stays TUI-hosted
        "skill": Exempt("live voting session is TUI-hosted by design; history stays readable via poker_history"),
        "desktop": {"/humans/poker", "/humans/poker/new", "/humans/poker/board"},
    },
    "retro-board": {
        # carried_action_items_for_session: the headless carry-forward load (prior
        # retro's action items) the TUI/browser adapt for the review column.
        # history_providers/report_payload: the board's step-back through previous
        # retros — the same runs `retro_history` already reads, shaped as cards.
        "engines": {
            ("yeaboi.retro.engine", "generate_action_items"),
            ("yeaboi.retro.engine", "carried_action_items_for_session"),
            ("yeaboi.retro.engine", "history_providers"),
            ("yeaboi.retro.engine", "report_payload"),
        },
        "mcp_tools": {"retro_history", "retro_export"},  # carried data rides along in retro_history's report
        "tui_mode": "retro",
        "cli": {"retro"},  # history read-back + export; the live board itself stays TUI-hosted
        "skill": Exempt("live board is TUI-only by design; history stays readable via retro_history"),
        "desktop": {"/humans/retro", "/humans/retro/board"},
    },
    "team-learning": {
        "engines": Exempt("lives in tools/team_learning.py as @tool functions — covered by test_tools_registry"),
        "mcp_tools": {"team_profile_get", "team_compare_plan_to_actuals"},
        "tui_mode": Exempt("profiles are consumed inside the planning/analysis screens, no dedicated card"),
        "cli": {"--team-profile", "--retro"},  # --learn moved to team-analysis (drives its engine now)
        "skill": Exempt("no plugin skill yet — tracked gap"),
        "desktop": Exempt("consumed inside the planning/analysis panels, same as the TUI — no dedicated page"),
    },
    "team-analysis": {
        "engines": {
            ("yeaboi.analysis.engine", "run_team_analysis"),
            ("yeaboi.analysis.engine", "get_team_roster"),
            ("yeaboi.analysis.engine", "get_team_roster_result"),
        },
        "mcp_tools": {"team_analyze", "team_roster"},
        "tui_mode": "team-analysis",
        "cli": {"analyze", "--learn"},
        "skill": "team-analysis",
        "desktop": {"/humans/analysis", "/humans/analysis/new", "/humans/analysis/results"},
    },
    "roadmap": {
        # Landed on main (TUI-only) before both this parity framework and the
        # MCP surface existed; the non-TUI surfaces are visible tracked gaps,
        # not silent ones — a follow-up should add a roadmap_analyze tool + CLI.
        "engines": {
            ("yeaboi.roadmap.engine", "run_roadmap_analysis"),
            ("yeaboi.roadmap.engine", "intake_mode_for"),
        },
        "mcp_tools": Exempt("no roadmap_analyze tool yet — tracked follow-up gap (newer than the MCP surface)"),
        "tui_mode": Exempt("a Planning intake card in _INTAKE_CARDS (Chat/Roadmap/Offline), not a mode card"),
        "cli": Exempt("interactive source picker + intake handoff; a headless roadmap path is a tracked gap"),
        "skill": Exempt("no plugin skill yet — tracked follow-up gap"),
        # Slipped M5 deliberately, and the ledger says so rather than quietly
        # carrying a milestone that has shipped: the intake tile needs a
        # roadmap path that is not TUI-only, and no surface has one yet — the
        # same gap the four rows above already track.
        "desktop": {"/humans/planning/roadmap"},
    },
    "anonymize": {
        # Post-processing action, not a mode of its own: an "Anonymize" button on every
        # mode's result screen masks the already-rendered output. The engine + MCP tool
        # give it real headless reach; the TUI-card/CLI/skill surfaces are deliberate gaps.
        "engines": {("yeaboi.anonymize.engine", "run_anonymize")},
        "mcp_tools": {"anonymize_text"},
        "tui_mode": Exempt("an action button on every mode's result screen, not a _MODE_CARDS entry"),
        "cli": Exempt("headless callers anonymize via the anonymize_text MCP tool"),
        "skill": Exempt("post-processing action, not a guided workflow"),
        "desktop": {"action:anonymize"},
    },
    "artifact-editing": {
        # Reader-authored corrections to a generated artifact. The browser half
        # is an action on the existing Share Online screen; the engine and the
        # MCP tools give it real headless reach, so an agent can fix a wrong
        # name before the report goes out and gets the same validation, caps and
        # allowlist the teammate in the browser would have.
        "engines": {
            ("yeaboi.artifacts.engine", "artifact_fields"),
            ("yeaboi.artifacts.engine", "artifact_edit_history"),
            ("yeaboi.artifacts.engine", "apply_artifact_edits"),
        },
        "mcp_tools": {"artifact_fields", "artifact_edit_history", "artifact_edit_apply"},
        "tui_mode": Exempt("editing happens on the shared browser document during a Share Online session"),
        "cli": Exempt("a JSON-patch flag would be an unusable surface; headless callers use the MCP tools"),
        "skill": Exempt("correcting one field is a single MCP call, not a multi-step guided workflow"),
        "desktop": {"action:edit-artifact"},
    },
    "output-sharing": {
        "engines": Exempt("transport over already-generated HTML artifacts, not an artifact-generation pipeline"),
        "mcp_tools": Exempt(
            "a stdio tool cannot safely own an interactive host process and temporary tunnel lifecycle"
        ),
        "tui_mode": Exempt("a Share Online action on existing result screens, not a dedicated mode card"),
        "cli": Exempt("temporary shares intentionally remain visible and cancellable in the interactive TUI"),
        "skill": Exempt("local process and access-code ownership belongs to the human host in the TUI"),
        "desktop": {"dialog:share", "dialog:export"},
    },
    "usage": {
        "engines": Exempt("TUI utility page — reads the local token_usage table"),
        "mcp_tools": {"usage_get"},
        "tui_mode": "usage",
        "cli": Exempt("TUI utility page; headless callers read usage_get over MCP"),
        "skill": Exempt("TUI utility page"),
        "desktop": {"/usage"},
    },
    "settings": {
        "engines": {
            ("yeaboi.settings.engine", "get_settings"),
            ("yeaboi.settings.engine", "set_setting"),
            ("yeaboi.settings.engine", "set_allowed_paths"),
            ("yeaboi.settings.engine", "set_data_dir"),
            ("yeaboi.settings.engine", "provider_catalog"),
            ("yeaboi.settings.engine", "verify_provider"),
            ("yeaboi.settings.engine", "discover_models"),
        },
        "mcp_tools": Exempt("MCP servers must not rewrite host credentials"),
        "tui_mode": "settings",
        "cli": {"--setup", "--theme", "--allow-path", "--list-audio-devices", "--install-voice", "--setup-access"},
        "skill": Exempt("TUI utility page"),
        "desktop": {"/settings/credentials", "/settings/sharing", "/settings/system", "/setup"},
    },
    # Ceremonies are the clock other modes run on, not a mode of their own: the
    # engine fires a catalogued mode and delivers its output.
    "ceremonies": {
        "engines": {("yeaboi.ceremonies.engine", "run_ceremony")},
        # Read-only on this surface, for the same reason the standup's own
        # schedule has never been settable here: declaring one installs a
        # launchd/crontab job on the user's machine that outlives the session,
        # survives reboots and spends money unattended.
        "mcp_tools": {"ceremonies_list", "ceremonies_history"},
        # Not a mode card: the menu draws every card with no scrolling, and an
        # eleventh pushes the version/changelog/feedback row off screen at the
        # enforced 84x40 minimum (measured) — trading three affordances every
        # user has for one menu entry. Reached by `s` from the welcome screen,
        # beside the changelog and feedback keycaps it belongs with.
        "tui_mode": Exempt("a welcome-screen keycap (s), not a card — an 11th card breaks the 84x40 layout"),
        "cli": {"ceremonies"},
        "skill": "ceremonies",
        # A full page rather than a card: the 84x40 constraint that kept it off
        # the TUI menu does not exist here.
        "desktop": {"/ceremonies"},
    },
    # The inbound half of that clock: a team reacting or replying in Slack, read
    # back on a schedule and applied to the run the post was about.
    "slack-inbound": {
        "engines": {
            ("yeaboi.slack.engine", "apply_inbound_events"),
            ("yeaboi.slack.engine", "inbound_history"),
            ("yeaboi.slack.engine", "link_slack_member"),
        },
        # Read-only on MCP for a sharper reason than ceremonies': the allowlist
        # that authorises an event lives in the poller, so this engine INHERITS
        # authorisation rather than checking it. An apply tool would let any MCP
        # client fabricate an event and drive a verdict or a pause with no Slack
        # in the loop at all. `link_slack_member` is absent for a different
        # reason — it is safe, but it decides whose name goes on somebody else's
        # report, which is the one binding Slack did not attest.
        "mcp_tools": {"slack_inbound_history", "slack_identities_list"},
        "tui_mode": Exempt("a Slack column and a link hint on the Ceremonies page — no 11th card (84x40)"),
        "cli": {"slack"},
        "skill": "slack-inbound",
        "desktop": {"/ceremonies/slack"},
    },
    # ── The Agents family (agentwatch) — cards live on the Agents menu
    # (_AGENT_CARDS), a sibling list of _MODE_CARDS behind the landing split.
    # Every mode ships at full parity: no Exempt entries, because each mode's
    # engine/MCP/CLI/skill surfaces landed in the same phase commit that
    # created its card.
    "agent-usage": {
        "engines": {("yeaboi.agentwatch.engine", "run_agent_usage")},
        # agents_usage_history is a read-only store wrapper (no pipeline) —
        # parity with reporting_history / retro_history.
        "mcp_tools": {"agents_usage", "agents_usage_history"},
        "tui_mode": "agent-usage",
        "cli": {"agents"},
        "skill": "agents-usage",
        "desktop": {"/agents/usage"},
    },
    "agent-advisor": {
        # advisor.py, not engine.py: the mirrored engine surface is served by
        # the Go sidecar, and the advisor pipeline is deliberately Python-only
        # (see advisor.py's module docstring) — a separate module keeps the
        # dual-maintenance boundary visible.
        "engines": {("yeaboi.agentwatch.advisor", "run_agent_advisor")},
        "mcp_tools": {"agents_advisor_run", "agents_advisor_history"},
        "tui_mode": "agent-advisor",
        "cli": {"agents"},
        "skill": "agents-advisor",
        "desktop": {"/agents/advisor"},
    },
    "agent-standup": {
        "engines": {("yeaboi.agentwatch.engine", "run_agent_standup")},
        "mcp_tools": {"agents_standup_run", "agents_standup_history"},
        "tui_mode": "agent-standup",
        "cli": {"agents"},
        "skill": "agents-standup",
        "desktop": {"/agents/standup"},
    },
    "agent-security": {
        "engines": {("yeaboi.agentwatch.engine", "run_agent_security")},
        "mcp_tools": {"agents_security_scan", "agents_security_history"},
        "tui_mode": "agent-security",
        "cli": {"agents"},
        "skill": "agents-security",
        "desktop": {"/agents/security"},
    },
    "provenance": {
        "engines": {
            ("yeaboi.provenance.engine", "run_provenance_audit"),
            ("yeaboi.provenance.engine", "trace_entity"),
        },
        "mcp_tools": {"provenance_audit", "provenance_trace"},
        "tui_mode": Exempt(
            "the chain records itself during standup/performance runs and its cards render inside "
            "those modes (the standup Conflicts card); the verify/trace surface is CLI/MCP-first — "
            "a dedicated TUI card is a tracked gap"
        ),
        "cli": {"provenance"},
        "skill": "provenance",
        # The audit + trace explorer — the first surface with a page for this.
        "desktop": {"/provenance"},
    },
    "ship": {
        # The supervised story → PR pipeline. run_ship is the one entry point;
        # the MCP surface is read-only by design — launching holds a live
        # subprocess for many minutes behind the server's engine lock, and the
        # approval gate is a human decision made at a terminal (the
        # output-sharing precedent).
        # resume_ship is the second entry point, not a second capability: it
        # re-enters the same pipeline for a run abandoned at the gate, which is
        # otherwise stranded (only the engine writes `approved` and opens the PR).
        # run_ship_batch is the third: one launch, N stacked story runs, for an
        # epic shipped as one PR per story (`ship run <EPIC> --split`).
        "engines": {
            ("yeaboi.ship.engine", "run_ship"),
            ("yeaboi.ship.engine", "resume_ship"),
            ("yeaboi.ship.engine", "run_ship_batch"),
        },
        "mcp_tools": {"ship_history", "ship_status"},
        "tui_mode": "ship",
        "cli": {"ship"},
        "skill": "ship",
        # Present, but story-level only: the desktop cannot yet target an epic
        # or a task, nor split an epic into stacked PRs. A route-set check
        # cannot see a narrowing inside a route, so it is named here and in
        # contracts/v1/app_http.md.
        "desktop": {"/humans/ship", "/humans/ship/run"},
    },
}

# Engine modules discovered by convention: every src/yeaboi/*/engine.py, plus
# the planning pipeline which (for LangGraph reasons) lives in agent/headless.py
# and the advisor pipeline which lives outside agentwatch/engine.py to stay off
# the Go-mirrored surface.
EXTRA_ENGINE_MODULES = {
    "yeaboi.agent.headless": SRC / "agent" / "headless.py",
    "yeaboi.agentwatch.advisor": SRC / "agentwatch" / "advisor.py",
}

# ---------------------------------------------------------------------------
# Param parity: MCP tool ↔ engine signature.
# ---------------------------------------------------------------------------

# Which engine entry point each engine-backed MCP tool wraps. Store-read tools
# (standup_history, retro_history, sessions_*, team_*) have no pipeline pair.
PARAM_PAIRS: dict[str, tuple[str, str]] = {
    "plan_generate": ("yeaboi.agent.headless", "run_planning_pipeline"),
    "standup_run": ("yeaboi.standup.engine", "run_standup"),
    "standup_review": ("yeaboi.standup.engine", "run_transcript_review"),
    "report_delivery": ("yeaboi.reporting.engine", "run_delivery_report"),
    "perf_one_on_one_prep": ("yeaboi.performance.engine", "run_one_on_one_prep"),
    "perf_one_on_one_complete": ("yeaboi.performance.engine", "complete_one_on_one"),
    "perf_six_month_review": ("yeaboi.performance.engine", "run_six_month_review"),
    "team_analyze": ("yeaboi.analysis.engine", "run_team_analysis"),
    "anonymize_text": ("yeaboi.anonymize.engine", "run_anonymize"),
    "agents_usage": ("yeaboi.agentwatch.engine", "run_agent_usage"),
    "agents_advisor_run": ("yeaboi.agentwatch.advisor", "run_agent_advisor"),
    "agents_standup_run": ("yeaboi.agentwatch.engine", "run_agent_standup"),
    "agents_security_scan": ("yeaboi.agentwatch.engine", "run_agent_security"),
    "provenance_audit": ("yeaboi.provenance.engine", "run_provenance_audit"),
    "provenance_trace": ("yeaboi.provenance.engine", "trace_entity"),
}

# Injection/test seams that are never exposed on any wire surface.
# ``on_run_id`` joins them for the same reason as ``on_progress``: it hands a
# caller-side callback the id of the run being started, which only a surface
# that owns the process can use. ``on_agent_line`` is the same shape — it
# streams the coding agent's live output to a caller that owns the process (the
# ship board), and is meaningless on a CLI flag or an MCP wire.
HIDDEN_ALWAYS = {"db_path", "today", "on_progress", "on_run_id", "on_agent_line", "dry_run"}

# Per-tool engine params deliberately not exposed on the MCP tool. Every entry
# needs a reason; a stale entry (param gone from the engine) fails the tests.
HIDDEN_PARAMS: dict[str, dict[str, str]] = {
    "plan_generate": {
        "questionnaire": "adapter — built from description/answers/project_context",
        "session_id": "plan_generate always mints a fresh session; the id is returned in data",
        "save_session": "MCP plans are always persisted — the session id IS the handle",
        "max_steps": "internal runaway-loop guard, not a user knob",
    },
    "team_analyze": {
        "progress": "injected adapter — the tool bridges it to ctx.report_progress notifications",
        "team_name": "AzDO team label; MCP auto-resolves it from the configured AZURE_DEVOPS_TEAM",
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; meaningless over the MCP wire",
    },
    "report_delivery": {
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; meaningless over the MCP wire",
    },
}

# Tool params with no engine counterpart — adapter inputs the tool assembles
# into the engine's arguments.
TOOL_ONLY_PARAMS: dict[str, set[str]] = {
    "plan_generate": {"description", "answers", "team_size", "sprint_length_weeks", "project_context"},
    # file_issues is an adapter over the SECOND engine entry point
    # (file_transcript_issues): run_transcript_review deliberately has no such
    # param, so the drafting path structurally cannot publish.
    "standup_review": {"file_issues"},
}

# ---------------------------------------------------------------------------
# Param parity: CLI subcommand ↔ engine signature (the same drift guard as
# PARAM_PAIRS, for the `yeaboi <command>` surface — CLI-only gaps shipped
# because only the MCP side was enforced).
# ---------------------------------------------------------------------------

# Which engine each headless subcommand drives ("perf prep" = nested path).
# The planning capability's CLI is the flat --non-interactive flag set, which
# predates subcommands and maps through QuestionnaireState — not pairable here.
CLI_PARAM_PAIRS: dict[str, tuple[str, str]] = {
    "report": ("yeaboi.reporting.engine", "run_delivery_report"),
    "standup": ("yeaboi.standup.engine", "run_standup"),
    "standup-review": ("yeaboi.standup.engine", "run_transcript_review"),
    "perf prep": ("yeaboi.performance.engine", "run_one_on_one_prep"),
    "perf complete": ("yeaboi.performance.engine", "complete_one_on_one"),
    "perf review": ("yeaboi.performance.engine", "run_six_month_review"),
    "analyze": ("yeaboi.analysis.engine", "run_team_analysis"),
    "agents cost": ("yeaboi.agentwatch.engine", "run_agent_usage"),
    "agents standup": ("yeaboi.agentwatch.engine", "run_agent_standup"),
    "agents security": ("yeaboi.agentwatch.engine", "run_agent_security"),
    "ship run": ("yeaboi.ship.engine", "run_ship"),
    "ship resume": ("yeaboi.ship.engine", "resume_ship"),
}

# CLI dest → engine param renames (the CLI keeps short ergonomic flag names).
CLI_RENAMES: dict[str, dict[str, str]] = {
    "report": {"session": "session_id", "label": "period_label_override"},
    "standup": {"session": "session_id"},
    # --transcript/--date carry explicit dest= in cli.py, so only --session
    # needs a rename here.
    "standup-review": {"session": "session_id"},
    "perf prep": {"session": "session_id"},
    "perf complete": {"session": "session_id"},
    "perf review": {"session": "session_id", "months": "period_months"},
    "ship run": {"session": "session_id", "check": "check_command"},
    "ship resume": {"check": "check_command"},
    "analyze": {
        "project": "project_key",
        "sprints": "sprint_count",
        "depth": "analysis_depth",
        "window_days": "analysis_window_days",
        "features": "analysis_features",
        "samples": "generate_samples",
        "no_insights": "include_insights",  # inverted store_true flag
    },
}

# CLI dests with no engine counterpart — output/dispatch concerns.
CLI_ONLY_DESTS: dict[str, set[str]] = {
    # source/code_sources/documentation_sources are assembled into the engine's
    # `sources` dict (component → source list), mirroring analyze's components flags.
    "report": {"format", "strict", "source", "code_sources", "documentation_sources"},
    "standup": {
        "format",
        "strict",
        "schedule",
        "list_members",
    },  # schedule/list-members are adapters, not run_standup params
    # file-issues drives the separate file_transcript_issues entry point;
    # list-gaps is a store read. `paths` is the bare positional form of
    # --transcript ("yeaboi standup-review meeting.vtt", and what a dragged file
    # produces) — the handler folds it into transcript_paths, and a lone "-" into
    # transcript_text.
    "standup-review": {"format", "strict", "file_issues", "list_gaps", "paths"},
    "perf prep": {"strict"},
    "perf complete": {"strict"},
    "perf review": {"strict"},
    "agents cost": {"format", "strict"},
    "agents standup": {"format", "strict"},
    "agents security": {"format", "strict"},
    # --split picks the entry point (run_ship_batch) rather than a run_ship param.
    "ship run": {"format", "strict", "split"},
    "ship resume": {"format", "strict"},
    # delivery/code/docs are assembled into the engine's `components` dict (component
    # → sub-source map); each flag names a component's sub-sources, not an engine param.
    "analyze": {
        "format",
        "strict",
        "delivery",
        "code",
        "docs",
        "github_owner",
        "azdo_code_project",
        "confluence_space",
        "notion_root",
    },
}

# Engine params deliberately without a CLI flag. Reasoned; staleness-checked.
CLI_HIDDEN: dict[str, dict[str, str]] = {
    "report": {
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; the CLI cancels via Ctrl-C",
        "sources": "assembled from the --source/--code-sources/--documentation-sources flags",
    },
    "analyze": {
        "progress": "live shared-list progress feed for the TUI frame loop — the CLI prints a banner instead",
        "team_name": "AzDO team label; auto-resolved from the configured AZURE_DEVOPS_TEAM",
        "components": "assembled from per-component --delivery/--code/--docs sub-source flags",
        "analysis_scope": "assembled from the four provider-specific scope flags",
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; the CLI cancels via Ctrl-C",
    },
    "ship run": {
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; the CLI cancels via Ctrl-C",
        "driver": "AgentDriver injection seam for tests; every wire surface runs the real Claude Code driver",
        "base_ref": "batch plumbing — run_ship_batch stacks each story on the branch before it",
        "pr_base": "batch plumbing — the stacked PR's target branch, set by run_ship_batch",
        "batch_id": "batch bookkeeping stamped by run_ship_batch; --split is how a user asks for one",
        "batch_item_id": "batch bookkeeping stamped by run_ship_batch — the epic its members came from",
        "batch_index": "batch bookkeeping stamped by run_ship_batch",
        "batch_total": "batch bookkeeping stamped by run_ship_batch",
    },
    "ship resume": {
        "cancel_event": "in-process threading.Event cancel seam for the TUI worker; the CLI cancels via Ctrl-C",
        "driver": "AgentDriver injection seam for tests; every wire surface runs the real Claude Code driver",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _non_exempt(field: str) -> dict[str, object]:
    """capability → value for every capability whose *field* is not Exempt."""
    return {cap: row[field] for cap, row in CAPABILITIES.items() if not isinstance(row[field], Exempt)}


def _module_to_path(module: str) -> pathlib.Path:
    if module in EXTRA_ENGINE_MODULES:
        return EXTRA_ENGINE_MODULES[module]
    return SRC.parent / pathlib.Path(module.replace(".", "/")).with_suffix(".py")


def _public_defs(path: pathlib.Path) -> set[str]:
    """Top-level public function names in *path* via AST — no import, no side effects."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    }


def _mcp_app():
    """The real FastMCP app (skips when the [mcp] extra isn't installed)."""
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from yeaboi.mcp.server import create_app

    app = create_app()
    assert hasattr(app, "_tool_manager"), (
        "the mcp SDK renamed FastMCP._tool_manager — update the introspection in test_surface_parity.py"
    )
    return app


def _tool_params(app, name: str) -> set[str]:
    """The client-visible parameter names of an MCP tool (ctx already excluded)."""
    tool = app._tool_manager.get_tool(name)
    return set(tool.parameters.get("properties", {}))


def _engine_params(module: str, fn: str) -> set[str]:
    import importlib
    import inspect

    return set(inspect.signature(getattr(importlib.import_module(module), fn)).parameters)


# ---------------------------------------------------------------------------
# Registry hygiene
# ---------------------------------------------------------------------------


class TestRegistryHygiene:
    def test_every_row_has_all_surfaces(self):
        required = {"engines", "mcp_tools", "tui_mode", "cli", "skill", "desktop"}
        for cap, row in CAPABILITIES.items():
            assert set(row) == required, f"capability {cap!r} must declare exactly the surfaces {sorted(required)}"

    def test_exempt_reasons_are_meaningful(self):
        for cap, row in CAPABILITIES.items():
            for field, value in row.items():
                if isinstance(value, Exempt):
                    assert len(value.reason) > 10, f"{cap}.{field}: Exempt needs a real reason, got {value.reason!r}"

    def test_hidden_params_have_reasons(self):
        for tool, params in HIDDEN_PARAMS.items():
            assert tool in PARAM_PAIRS, f"HIDDEN_PARAMS names unknown tool {tool!r}"
            for param, reason in params.items():
                assert len(reason) > 10, f"{tool}.{param}: hidden param needs a real reason"


# ---------------------------------------------------------------------------
# 1. Engine discovery — every engine module + entry point is registered
# ---------------------------------------------------------------------------


class TestEngines:
    def test_engine_modules_registered(self):
        discovered = {f"yeaboi.{p.parent.name}.engine" for p in SRC.glob("*/engine.py")}
        discovered |= set(EXTRA_ENGINE_MODULES)
        registered = {mod for entries in _non_exempt("engines").values() for mod, _fn in entries}
        assert discovered == registered, (
            f"engine modules on disk vs registered in CAPABILITIES differ.\n"
            f"  unregistered new engines: {sorted(discovered - registered)}\n"
            f"  registered but missing on disk: {sorted(registered - discovered)}\n{_HOW_TO}"
        )

    def test_engine_entry_points_registered(self):
        registered_by_module: dict[str, set[str]] = {}
        for entries in _non_exempt("engines").values():
            for mod, fn in entries:
                registered_by_module.setdefault(mod, set()).add(fn)
        for mod, registered_fns in registered_by_module.items():
            public = _public_defs(_module_to_path(mod))
            assert public == registered_fns, (
                f"public entry points of {mod} vs CAPABILITIES differ.\n"
                f"  new unregistered functions: {sorted(public - registered_fns)}\n"
                f"  registered but gone: {sorted(registered_fns - public)}\n{_HOW_TO}"
            )


# ---------------------------------------------------------------------------
# 2. MCP tool inventory
# ---------------------------------------------------------------------------


class TestMcpTools:
    def test_tool_inventory_registered(self):
        app = _mcp_app()
        actual = {t.name for t in app._tool_manager.list_tools()}
        registered = {name for names in _non_exempt("mcp_tools").values() for name in names}
        assert actual == registered, (
            f"MCP server tools vs CAPABILITIES differ.\n"
            f"  new unregistered tools: {sorted(actual - registered)}\n"
            f"  registered but not on the server: {sorted(registered - actual)}\n{_HOW_TO}"
        )


# ---------------------------------------------------------------------------
# 3. TUI mode cards
# ---------------------------------------------------------------------------


class TestTuiModes:
    def test_mode_cards_registered(self):
        # The union of both category menus — Humans (_MODE_CARDS) and Agents
        # (_AGENT_CARDS) — must equal the registered tui_mode column.
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

        actual = {card["key"] for card in (*_MODE_CARDS, *_AGENT_CARDS)}
        registered = set(_non_exempt("tui_mode").values())
        assert actual == registered, (
            f"_MODE_CARDS/_AGENT_CARDS keys vs CAPABILITIES differ.\n"
            f"  new unregistered cards: {sorted(actual - registered)}\n"
            f"  registered but card removed: {sorted(registered - actual)}\n{_HOW_TO}"
        )


# ---------------------------------------------------------------------------
# 3b. Discoverability tips — every capability surfaces a rotating tip on the
#     welcome screen, so tips stay current as features land. Model: the same
#     two-way set-equality as the mode-card check above.
# ---------------------------------------------------------------------------

# Capabilities that deliberately have no welcome-screen tip. Empty today — every
# capability is worth surfacing. Add a `key: "reason (>10 chars)"` entry to opt a
# capability out (TestTips enforces the reason length, like Exempt).
TIP_EXEMPT: dict[str, str] = {}

_TIP_HOW_TO = (
    "Fix: add a FeatureTip for this capability in src/yeaboi/ui/shared/_tips.py "
    "(_FEATURE_TIPS), or record a TIP_EXEMPT entry in tests/unit/test_surface_parity.py."
)


class TestTips:
    def test_every_capability_has_a_tip(self):
        from yeaboi.ui.shared._tips import _FEATURE_TIPS

        actual = {t.key for t in _FEATURE_TIPS}
        registered = set(CAPABILITIES) - set(TIP_EXEMPT)
        assert actual == registered, (
            f"welcome-screen feature tips vs CAPABILITIES differ.\n"
            f"  capabilities with no tip: {sorted(registered - actual)}\n"
            f"  tips for an unknown/exempt capability: {sorted(actual - registered)}\n{_TIP_HOW_TO}"
        )

    def test_tip_exempt_reasons_are_meaningful(self):
        for cap, reason in TIP_EXEMPT.items():
            assert cap in CAPABILITIES, f"TIP_EXEMPT names unknown capability {cap!r}"
            assert len(reason) > 10, f"TIP_EXEMPT[{cap!r}] needs a real reason, got {reason!r}"

    def test_carded_capabilities_have_jump_targets(self):
        # Every capability that owns a mode card must have a tip whose mode_key
        # points at that exact card, so the jump-into-feature key can't rot.
        # Cards span both category menus (the `g` jump switches category).
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS
        from yeaboi.ui.shared._tips import _FEATURE_TIPS

        card_keys = {card["key"] for card in (*_MODE_CARDS, *_AGENT_CARDS)}
        by_key = {t.key: t for t in _FEATURE_TIPS}
        for cap, tui_mode in _non_exempt("tui_mode").items():
            tip = by_key.get(cap)
            assert tip is not None and tip.mode_key == tui_mode, (
                f"capability {cap!r} has mode card {tui_mode!r} but its tip's mode_key is "
                f"{getattr(tip, 'mode_key', None)!r} — jump-into-feature would miss.\n{_TIP_HOW_TO}"
            )
        # No tip may point at a non-existent card.
        for tip in _FEATURE_TIPS:
            assert tip.mode_key is None or tip.mode_key in card_keys, (
                f"tip {tip.key!r} jumps to unknown card {tip.mode_key!r}\n{_TIP_HOW_TO}"
            )


# ---------------------------------------------------------------------------
# 3c. Saved sessions — every mode card that records runs lands on a hub the
#     user can reopen finished work from. Model: the same two-way set-equality
#     as the tip check above, keyed on cards rather than capabilities.
# ---------------------------------------------------------------------------

# Cards that deliberately have no saved-sessions hub. A reason is required and is
# length-checked, like Exempt — "no time" must not pass for "nothing to save".
SAVED_SESSIONS_EXEMPT: dict[str, str] = {
    "team-analysis": "saved analyses ARE the card's landing list (_build_project_list_screen)",
    "project-planning": "saved projects and roadmaps ARE the card's landing list",
    "performance": "artifacts are per-engineer — the hub opens from the roster's History action",
    "usage": "a live dashboard over the usage DB; every view already spans the whole history",
    "settings": "a live config editor — there is no completed session to re-open",
    "agent-usage": "opens instantly on the last saved report (AgentWatchStore.latest_report); "
    "list_reports exists and a browsable hub is queued follow-up work",
    "agent-advisor": "opens instantly on the last saved report (AgentWatchStore.latest_report); "
    "list_reports exists and a browsable hub is queued follow-up work",
    "agent-standup": "opens instantly on the last saved report (AgentWatchStore.latest_report); "
    "list_reports exists and a browsable hub is queued follow-up work",
    "agent-security": "opens instantly on the last saved report (AgentWatchStore.latest_report); "
    "list_reports exists and a browsable hub is queued follow-up work",
}

_SAVED_SESSIONS_HOW_TO = (
    "Fix: give the card a saved-sessions hub — wire its dispatch branch through "
    "_run_mode_hub(...) in src/yeaboi/ui/mode_select/__init__.py and register it in "
    "SAVED_SESSION_HUBS there — or record a SAVED_SESSIONS_EXEMPT entry in "
    "tests/unit/test_surface_parity.py saying why this card has nothing to re-open."
)


class TestSavedSessions:
    """A finished run the user cannot get back to is a run they lost.

    Five of the mode cards grew a saved-runs hub organically and Ship shipped
    without one, which is exactly the failure mode a registry check exists for:
    the requirement lived only in the habit of copying a neighbouring mode.
    """

    def _card_keys(self) -> set[str]:
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

        return {card["key"] for card in (*_MODE_CARDS, *_AGENT_CARDS)}

    def test_every_card_lands_on_a_saved_sessions_hub(self):
        from yeaboi.ui.mode_select import SAVED_SESSION_HUBS

        expected = self._card_keys() - set(SAVED_SESSIONS_EXEMPT)
        actual = set(SAVED_SESSION_HUBS)
        assert actual == expected, (
            f"saved-sessions hubs vs mode cards differ.\n"
            f"  cards with no hub: {sorted(expected - actual)}\n"
            f"  hubs for an unknown/exempt card: {sorted(actual - expected)}\n{_SAVED_SESSIONS_HOW_TO}"
        )

    def test_registered_hubs_are_callable(self):
        from yeaboi.ui.mode_select import SAVED_SESSION_HUBS

        for key, hub in SAVED_SESSION_HUBS.items():
            assert callable(hub), f"SAVED_SESSION_HUBS[{key!r}] is not callable"

    def test_exempt_reasons_are_meaningful(self):
        card_keys = self._card_keys()
        for key, reason in SAVED_SESSIONS_EXEMPT.items():
            assert key in card_keys, f"SAVED_SESSIONS_EXEMPT names unknown mode card {key!r}"
            assert len(reason) > 10, f"SAVED_SESSIONS_EXEMPT[{key!r}] needs a real reason, got {reason!r}"


# ---------------------------------------------------------------------------
# 4. CLI flags — presence check (argparse can't tell us which new flag is
#    "a capability", so discovery in the reverse direction rides on the
#    engine/TUI/MCP checks) + the --mode ⊆ _MODE_CARDS drift guard.
# ---------------------------------------------------------------------------


class TestCli:
    def test_registered_flags_exist(self):
        from yeaboi.cli import build_parser

        parser = build_parser()
        option_strings = {s for action in parser._actions for s in action.option_strings}
        subcommands = set()
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices and not action.option_strings:
                subcommands |= set(action.choices)  # subparsers action
        available = option_strings | subcommands
        for cap, flags in _non_exempt("cli").items():
            missing = set(flags) - available
            assert not missing, (
                f"capability {cap!r} registers CLI entries the parser doesn't define: {sorted(missing)}\n{_HOW_TO}"
            )

    def test_mode_choices_subset_of_mode_cards(self):
        from yeaboi.cli import build_parser
        from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS

        parser = build_parser()
        mode_action = next(a for a in parser._actions if "--mode" in a.option_strings)
        card_keys = {card["key"] for card in _MODE_CARDS}
        drift = set(mode_action.choices) - card_keys
        assert not drift, f"--mode offers choices with no _MODE_CARDS entry: {sorted(drift)} — cli.py drifted"


# ---------------------------------------------------------------------------
# 5. Plugin skills
# ---------------------------------------------------------------------------


class TestPluginSkills:
    def test_skill_dirs_registered(self):
        actual = {p.parent.name for p in PLUGIN_SKILLS_DIR.glob("*/SKILL.md")}
        registered = set(_non_exempt("skill").values())
        assert actual == registered, (
            f"claude-plugin skills vs CAPABILITIES differ.\n"
            f"  new unregistered skills: {sorted(actual - registered)}\n"
            f"  registered but no SKILL.md: {sorted(registered - actual)}\n{_HOW_TO}"
        )

    def test_skills_mention_their_capability_tools(self):
        for cap, row in CAPABILITIES.items():
            if isinstance(row["skill"], Exempt) or isinstance(row["mcp_tools"], Exempt):
                continue
            body = (PLUGIN_SKILLS_DIR / row["skill"] / "SKILL.md").read_text(encoding="utf-8")
            referenced = set(re.findall(r"`([a-z][a-z0-9_]+)`", body))
            assert referenced & row["mcp_tools"], (
                f"skill {row['skill']!r} never mentions any of capability {cap!r}'s MCP tools "
                f"{sorted(row['mcp_tools'])} — the skill can't be driving this capability"
            )


# ---------------------------------------------------------------------------
# 6. Desktop — renderer routes/actions vs the committed manifest.
#
# The desktop renderer's route registry (desktop/src/renderer/routes.ts +
# actions.ts) is code-generated into contracts/v1/routes_manifest.json and
# committed, the same seam as frontend/ → web/static/ — so this Python suite
# never needs Node. A desktop-side test asserts manifest == registries; here we
# assert manifest == CAPABILITIES, two-way. Entries prefixed "action:" /
# "dialog:" are non-route affordances (result-screen buttons, dialogs).
# ---------------------------------------------------------------------------

DESKTOP_MANIFEST = REPO_ROOT / "contracts" / "v1" / "routes_manifest.json"


class TestDesktop:
    def _manifest_entries(self) -> list[dict]:
        import json

        data = json.loads(DESKTOP_MANIFEST.read_text(encoding="utf-8"))
        # v2 grew the settings tabs and the chat's command registry, both
        # checked in test_tui_parity.py — the routes below are unchanged.
        assert data.get("schema_version") == 2, "routes_manifest.json schema_version must be 2"
        return data["routes"]

    def test_manifest_entries_are_well_formed(self):
        for entry in self._manifest_entries():
            assert set(entry) == {"path", "capability", "title"}, f"malformed manifest entry: {entry}"
            assert entry["path"].startswith(("/", "action:", "dialog:")), entry["path"]
            assert entry["title"], f"manifest entry {entry['path']!r} needs a title"

    def test_routes_registered(self):
        actual = {entry["path"] for entry in self._manifest_entries() if entry["capability"]}
        registered = {path for paths in _non_exempt("desktop").values() for path in paths}
        assert actual == registered, (
            f"desktop manifest vs CAPABILITIES differ.\n"
            f"  in manifest but unregistered: {sorted(actual - registered)}\n"
            f"  registered but not in manifest: {sorted(registered - actual)}\n{_HOW_TO}"
        )

    def test_route_capabilities_match(self):
        by_cap = _non_exempt("desktop")
        for entry in self._manifest_entries():
            cap = entry["capability"]
            if cap is None:
                continue  # pure chrome (home, what's-new) — owned by no capability
            assert cap in CAPABILITIES, f"manifest route {entry['path']!r} names unknown capability {cap!r}"
            assert entry["path"] in by_cap.get(cap, set()), (
                f"manifest route {entry['path']!r} claims capability {cap!r}, "
                f"but that row's desktop column does not list it\n{_HOW_TO}"
            )


# ---------------------------------------------------------------------------
# 7. Param parity — the engine's keyword surface must reach the MCP tool
# ---------------------------------------------------------------------------


class TestParamParity:
    def test_every_engine_backed_tool_is_paired(self):
        registered_tools = {name for names in _non_exempt("mcp_tools").values() for name in names}
        unknown = set(PARAM_PAIRS) - registered_tools
        assert not unknown, f"PARAM_PAIRS names tools not in CAPABILITIES: {sorted(unknown)}"

    def test_engine_params_reach_the_tool(self):
        app = _mcp_app()
        problems: list[str] = []
        for tool_name, (mod, fn) in PARAM_PAIRS.items():
            tool_params = _tool_params(app, tool_name)
            engine_params = _engine_params(mod, fn)
            hidden = set(HIDDEN_PARAMS.get(tool_name, {}))
            unexposed = engine_params - tool_params - HIDDEN_ALWAYS - hidden
            if unexposed:
                problems.append(
                    f"{tool_name}: engine {mod}.{fn} grew params the MCP tool doesn't expose: "
                    f"{sorted(unexposed)} — expose them in src/yeaboi/mcp/tools_*.py or add "
                    f"them to HIDDEN_PARAMS with a reason"
                )
        assert not problems, "\n".join(problems) + f"\n{_HOW_TO}"

    def test_tool_params_map_to_the_engine(self):
        app = _mcp_app()
        problems: list[str] = []
        for tool_name, (mod, fn) in PARAM_PAIRS.items():
            tool_params = _tool_params(app, tool_name)
            engine_params = _engine_params(mod, fn)
            phantom = tool_params - engine_params - TOOL_ONLY_PARAMS.get(tool_name, set())
            if phantom:
                problems.append(
                    f"{tool_name}: tool params with no engine counterpart (typo'd rename?): {sorted(phantom)}"
                )
        assert not problems, "\n".join(problems) + f"\n{_HOW_TO}"

    def test_hidden_params_still_exist_on_engines(self):
        for tool_name, hidden in HIDDEN_PARAMS.items():
            mod, fn = PARAM_PAIRS[tool_name]
            engine_params = _engine_params(mod, fn)
            stale = set(hidden) - engine_params
            assert not stale, (
                f"{tool_name}: HIDDEN_PARAMS lists params the engine {mod}.{fn} no longer has: "
                f"{sorted(stale)} — delete the stale exemptions"
            )

    def test_tool_only_params_are_real(self):
        app = _mcp_app()
        for tool_name, extras in TOOL_ONLY_PARAMS.items():
            tool_params = _tool_params(app, tool_name)
            stale = extras - tool_params
            assert not stale, f"{tool_name}: TOOL_ONLY_PARAMS lists params the tool doesn't have: {sorted(stale)}"


# ---------------------------------------------------------------------------
# 7. Param parity — the engine's keyword surface must reach the CLI subcommand
# ---------------------------------------------------------------------------


def _cli_subparser(path: str):
    """Resolve 'report' or 'perf prep' to its argparse sub-parser."""
    import argparse

    from yeaboi.cli import build_parser

    p = build_parser()
    for part in path.split():
        action = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        p = action.choices[part]
    return p


def _cli_dests(path: str) -> set[str]:
    """The argument dests a subcommand defines (its own flags + positionals)."""
    import argparse

    return {
        a.dest
        for a in _cli_subparser(path)._actions
        if a.dest != "help" and not isinstance(a, argparse._SubParsersAction)
    }


class TestCliParamParity:
    def test_engine_params_reach_the_cli(self):
        problems: list[str] = []
        for path, (mod, fn) in CLI_PARAM_PAIRS.items():
            renames = CLI_RENAMES.get(path, {})
            mapped = {renames.get(d, d) for d in _cli_dests(path) - CLI_ONLY_DESTS[path]}
            engine_params = _engine_params(mod, fn)
            hidden = set(CLI_HIDDEN.get(path, {}))
            unexposed = engine_params - mapped - HIDDEN_ALWAYS - hidden
            if unexposed:
                problems.append(
                    f"yeaboi {path}: engine {mod}.{fn} has params the CLI doesn't expose: {sorted(unexposed)} — "
                    f"add flags in cli.py build_parser() or a CLI_HIDDEN entry with a reason"
                )
        assert not problems, "\n".join(problems) + f"\n{_HOW_TO}"

    def test_cli_dests_map_to_the_engine(self):
        problems: list[str] = []
        for path, (mod, fn) in CLI_PARAM_PAIRS.items():
            renames = CLI_RENAMES.get(path, {})
            mapped = {renames.get(d, d) for d in _cli_dests(path) - CLI_ONLY_DESTS[path]}
            phantom = mapped - _engine_params(mod, fn)
            if phantom:
                problems.append(
                    f"yeaboi {path}: CLI args with no engine counterpart (typo'd rename?): {sorted(phantom)}"
                )
        assert not problems, "\n".join(problems) + f"\n{_HOW_TO}"

    def test_cli_registry_entries_are_real(self):
        """Renames/CLI-only/hidden entries must not go stale as flags evolve."""
        assert set(CLI_ONLY_DESTS) == set(CLI_PARAM_PAIRS), "CLI_ONLY_DESTS must cover exactly CLI_PARAM_PAIRS"
        for path in CLI_PARAM_PAIRS:
            dests = _cli_dests(path)
            stale_renames = set(CLI_RENAMES.get(path, {})) - dests
            assert not stale_renames, (
                f"yeaboi {path}: CLI_RENAMES names dests that don't exist: {sorted(stale_renames)}"
            )
            stale_only = CLI_ONLY_DESTS[path] - dests
            assert not stale_only, f"yeaboi {path}: CLI_ONLY_DESTS names dests that don't exist: {sorted(stale_only)}"

    def test_cli_hidden_params_still_exist_on_engines(self):
        for path, hidden in CLI_HIDDEN.items():
            mod, fn = CLI_PARAM_PAIRS[path]
            stale = set(hidden) - _engine_params(mod, fn)
            assert not stale, (
                f"yeaboi {path}: CLI_HIDDEN lists params the engine {mod}.{fn} no longer has: "
                f"{sorted(stale)} — delete the stale exemptions"
            )
            for param, reason in hidden.items():
                assert len(reason) > 10, f"{path}.{param}: hidden param needs a real reason"
