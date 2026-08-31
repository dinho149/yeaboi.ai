"""The declarative route table — the desktop surface's discovery source.

Every native route the backend serves is one row here, and
:func:`build_router` is the only thing that turns rows into a live
:class:`~yeaboi.app.router.Router`. That makes this module the Python-side
parity anchor: ``tests/unit/test_surface_parity.py``'s desktop column (landing
with the renderer milestone) checks two-way against ``ROUTES`` plus the MCP
dispatcher's tool inventory, so a route added here without a capability — or a
capability claiming a route that does not exist — fails the build.

``capability`` names the ``CAPABILITIES`` row a route belongs to; ``None``
marks pure infrastructure (health, events, shutdown) that no capability owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from yeaboi.app import (
    routes_agents,
    routes_ambience,
    routes_analysis,
    routes_boards,
    routes_ceremonies,
    routes_chat,
    routes_consent,
    routes_feedback,
    routes_meta,
    routes_niko,
    routes_performance,
    routes_reporting,
    routes_roadmap,
    routes_settings,
    routes_share,
    routes_ship,
    routes_standup,
    routes_voice,
)
from yeaboi.app.router import Router

#: Routes that may answer without a bearer token. Kept as an explicit,
#: test-pinned allowlist — see ``health``'s docstring for why it qualifies.
UNAUTHENTICATED = frozenset({"/api/health"})


@dataclass(frozen=True)
class AppRoute:
    """One native route: where it lives, who handles it, what owns it."""

    method: str
    path: str
    handler: object  # Callable[(app, Request), Response] — bound by build_router
    capability: str | None = None


ROUTES: tuple[AppRoute, ...] = (
    AppRoute("GET", "/api/health", routes_meta.health),
    AppRoute("GET", "/api/meta/version", routes_meta.version),
    AppRoute("GET", "/api/meta/capabilities", routes_meta.capabilities),
    AppRoute("GET", "/api/meta/tips", routes_meta.tips),
    AppRoute("GET", "/api/meta/changelog", routes_meta.changelog),
    # Chrome like tips/changelog: no capability owns disclosure, and it is
    # deliberately never gated behind one — the privacy page must always answer.
    AppRoute("GET", "/api/meta/privacy", routes_meta.privacy),
    AppRoute("GET", "/api/system/check", routes_meta.system_check),
    AppRoute("GET", "/api/tools", routes_meta.tools),
    AppRoute("POST", "/api/tool/{name}", routes_meta.call_tool),
    AppRoute("GET", "/api/events", routes_meta.events),
    AppRoute("POST", "/api/ops/{op_id}/cancel", routes_meta.cancel_op),
    AppRoute("POST", "/api/shutdown", routes_meta.shutdown),
    # -- settings (capability "settings" — the M4 surface) -------------------
    AppRoute("GET", "/api/settings", routes_settings.get_settings, "settings"),
    AppRoute("GET", "/api/settings/providers", routes_settings.providers, "settings"),
    AppRoute("POST", "/api/settings/set", routes_settings.set_setting, "settings"),
    AppRoute("POST", "/api/settings/allowed-paths", routes_settings.allowed_paths, "settings"),
    AppRoute("POST", "/api/settings/data-dir", routes_settings.data_dir, "settings"),
    AppRoute("POST", "/api/settings/provider/verify", routes_settings.provider_verify, "settings"),
    AppRoute("POST", "/api/settings/provider/models", routes_settings.provider_models, "settings"),
    AppRoute("POST", "/api/settings/connection/verify", routes_settings.connection_verify, "settings"),
    AppRoute("GET", "/api/settings/access/state", routes_settings.access_state, "settings"),
    AppRoute("POST", "/api/settings/access/verify", routes_settings.access_verify, "settings"),
    AppRoute("POST", "/api/settings/signin/start", routes_settings.signin_start, "settings"),
    AppRoute("GET", "/api/settings/signin", routes_settings.signin_status, "settings"),
    AppRoute("POST", "/api/settings/signin/code", routes_settings.signin_code, "settings"),
    AppRoute("POST", "/api/settings/signin/cancel", routes_settings.signin_cancel, "settings"),
    # -- the planning chat (capability "planning" — the M5 surface) ----------
    AppRoute("POST", "/api/chat/sessions", routes_chat.create, "planning"),
    AppRoute("GET", "/api/chat/sessions/{project_id}", routes_chat.get, "planning"),
    AppRoute("POST", "/api/chat/sessions/{project_id}/send", routes_chat.send, "planning"),
    # The three the slash menu needs (M12): the question plan behind
    # /questions, /form and a bare /edit; the size switch behind /small and
    # /large; and the attachment store behind a pasted screenshot.
    AppRoute("GET", "/api/chat/sessions/{project_id}/questions", routes_chat.questions, "planning"),
    AppRoute("POST", "/api/chat/sessions/{project_id}/size", routes_chat.size, "planning"),
    AppRoute("POST", "/api/chat/sessions/{project_id}/attachments", routes_chat.attach, "planning"),
    # -- Niko, the global assistant (capability "niko") ----------------------
    # Chrome rather than a page: the panel opens over whatever route is showing,
    # which is why its desktop parity row claims `action:ask-niko` instead of a
    # path. Read-only end to end — there is no route here that changes anything.
    AppRoute("POST", "/api/niko/conversations", routes_niko.create, "niko"),
    AppRoute("GET", "/api/niko/conversations", routes_niko.conversations, "niko"),
    AppRoute("GET", "/api/niko/conversations/{conversation_id}", routes_niko.get, "niko"),
    AppRoute("POST", "/api/niko/conversations/{conversation_id}/send", routes_niko.send, "niko"),
    AppRoute("POST", "/api/niko/conversations/{conversation_id}/delete", routes_niko.delete, "niko"),
    AppRoute("GET", "/api/niko/suggestions", routes_niko.suggestions, "niko"),
    # -- the standup dashboard (capability "standup" — the M6 surface) -------
    AppRoute("GET", "/api/standup/dashboard", routes_standup.dashboard, "standup"),
    AppRoute("POST", "/api/standup/run", routes_standup.run, "standup"),
    AppRoute("POST", "/api/standup/runs/{run_id}/delete", routes_standup.delete_run, "standup"),
    AppRoute("GET", "/api/standup/schedule", routes_standup.schedule, "standup"),
    AppRoute("POST", "/api/standup/schedule", routes_standup.set_schedule, "standup"),
    # -- team analysis (capability "team-analysis" — the M6 surface) ---------
    AppRoute("GET", "/api/analysis/options", routes_analysis.options, "team-analysis"),
    AppRoute("POST", "/api/analysis/steps", routes_analysis.steps, "team-analysis"),
    AppRoute("GET", "/api/analysis/profiles", routes_analysis.profiles, "team-analysis"),
    AppRoute("GET", "/api/analysis/result/{team_id}", routes_analysis.result, "team-analysis"),
    AppRoute("POST", "/api/analysis/run", routes_analysis.run, "team-analysis"),
    # -- live boards (the M7 surface) ----------------------------------------
    # `boards`/`board`/`link`/`close` serve both kinds, so the row they belong
    # to is the kind whose board is open. Registered against retro-board, with
    # scrum-poker owning the poker-specific half.
    AppRoute("GET", "/api/boards", routes_boards.boards, "retro-board"),
    AppRoute("POST", "/api/boards/retro", routes_boards.start_retro, "retro-board"),
    AppRoute("GET", "/api/boards/{board_id}", routes_boards.board, "retro-board"),
    AppRoute("GET", "/api/boards/{board_id}/host", routes_boards.board_host, "retro-board"),
    AppRoute("POST", "/api/boards/{board_id}/link", routes_boards.retry_link, "retro-board"),
    AppRoute("GET", "/api/boards/{board_id}/invite", routes_boards.invite, "retro-board"),
    AppRoute("POST", "/api/boards/{board_id}/actions", routes_boards.generate_actions, "retro-board"),
    AppRoute("POST", "/api/boards/{board_id}/close", routes_boards.close_board, "retro-board"),
    AppRoute("POST", "/api/boards/poker", routes_boards.start_poker, "scrum-poker"),
    AppRoute("GET", "/api/poker/options", routes_boards.poker_options, "scrum-poker"),
    AppRoute("GET", "/api/poker/sprints", routes_boards.poker_sprints, "scrum-poker"),
    AppRoute("GET", "/api/poker/types", routes_boards.poker_types, "scrum-poker"),
    AppRoute("POST", "/api/poker/tickets", routes_boards.poker_tickets, "scrum-poker"),
    # -- export / share / anonymize, on every result screen (the M7 surface) --
    AppRoute("GET", "/api/export/destinations", routes_share.destinations, "output-sharing"),
    AppRoute("POST", "/api/export", routes_share.export, "output-sharing"),
    AppRoute("GET", "/api/shares", routes_share.shares, "output-sharing"),
    AppRoute("POST", "/api/shares", routes_share.start_share, "output-sharing"),
    AppRoute("GET", "/api/shares/{share_id}", routes_share.share, "output-sharing"),
    AppRoute("GET", "/api/shares/{share_id}/invite", routes_share.share_invite, "output-sharing"),
    AppRoute("POST", "/api/shares/{share_id}/discard", routes_share.discard_edits, "artifact-editing"),
    AppRoute("POST", "/api/shares/{share_id}/close", routes_share.stop_share, "output-sharing"),
    AppRoute("GET", "/api/artifacts/kinds", routes_share.artifact_kinds, "output-sharing"),
    AppRoute("GET", "/api/artifacts/{kind}/edits", routes_share.artifact_edits, "artifact-editing"),
    AppRoute("POST", "/api/anonymize", routes_share.anonymize, "anonymize"),
    # -- reporting (the M8 surface) ------------------------------------------
    AppRoute("GET", "/api/reporting/options", routes_reporting.options, "reporting"),
    AppRoute("GET", "/api/reporting/sprints", routes_reporting.sprints, "reporting"),
    AppRoute("POST", "/api/reporting/window", routes_reporting.window, "reporting"),
    AppRoute("POST", "/api/reporting/run", routes_reporting.run, "reporting"),
    AppRoute("POST", "/api/reporting/style", routes_reporting.style, "reporting"),
    AppRoute("POST", "/api/reporting/fit", routes_reporting.fit, "reporting"),
    AppRoute("POST", "/api/reporting/export", routes_reporting.export_deck, "reporting"),
    # -- performance (the M8 surface) ----------------------------------------
    AppRoute("GET", "/api/performance/roster", routes_performance.roster, "performance"),
    AppRoute("GET", "/api/performance/engineer/{name}", routes_performance.engineer, "performance"),
    # -- roadmap intake (the M8 surface) -------------------------------------
    AppRoute("GET", "/api/roadmap/options", routes_roadmap.options, "roadmap"),
    AppRoute("GET", "/api/roadmap/saved", routes_roadmap.roadmaps, "roadmap"),
    AppRoute("GET", "/api/roadmap/saved/{roadmap_id}", routes_roadmap.roadmap, "roadmap"),
    AppRoute("POST", "/api/roadmap/analyze", routes_roadmap.analyze, "roadmap"),
    AppRoute("POST", "/api/roadmap/plan", routes_roadmap.plan, "roadmap"),
    # -- ship (the M8 surface) -----------------------------------------------
    AppRoute("GET", "/api/ship/stories", routes_ship.stories, "ship"),
    AppRoute("POST", "/api/ship/target", routes_ship.target, "ship"),
    AppRoute("GET", "/api/ship/runs", routes_ship.runs, "ship"),
    AppRoute("POST", "/api/ship/runs", routes_ship.launch, "ship"),
    AppRoute("GET", "/api/ship/runs/{key}", routes_ship.run, "ship"),
    AppRoute("POST", "/api/ship/runs/{key}/gate", routes_ship.gate, "ship"),
    AppRoute("POST", "/api/ship/runs/{key}/cancel", routes_ship.cancel, "ship"),
    # -- ceremonies + the inbound Slack lane (the M9 surface) -----------------
    AppRoute("GET", "/api/ceremonies", routes_ceremonies.ceremonies, "ceremonies"),
    AppRoute("POST", "/api/ceremonies", routes_ceremonies.declare, "ceremonies"),
    AppRoute("POST", "/api/ceremonies/{name}/enabled", routes_ceremonies.enabled, "ceremonies"),
    AppRoute("POST", "/api/ceremonies/{name}/remove", routes_ceremonies.remove, "ceremonies"),
    AppRoute("POST", "/api/ceremonies/{name}/run", routes_ceremonies.run, "ceremonies"),
    AppRoute("GET", "/api/slack", routes_ceremonies.slack, "slack-inbound"),
    AppRoute("POST", "/api/slack/link", routes_ceremonies.link, "slack-inbound"),
    AppRoute("POST", "/api/slack/poll", routes_ceremonies.poll, "slack-inbound"),
    # -- the Agents family (the M9 surface) ----------------------------------
    # One set of routes over four modes, addressed by kind. Registered against
    # agent-usage, the row whose engine the other three sit beside.
    AppRoute("GET", "/api/agents/modes", routes_agents.modes, "agent-usage"),
    AppRoute("GET", "/api/agents/{kind}/latest", routes_agents.latest, "agent-usage"),
    AppRoute("POST", "/api/agents/{kind}/run", routes_agents.run, "agent-usage"),
    AppRoute("POST", "/api/agents/{kind}/export", routes_agents.export, "agent-usage"),
    # -- the shell's own furniture (the M10 surface) --------------------------
    # No capability owns these: ambience, the beta gate, the feedback form and
    # the sandbox consent modal are things the shell needs to draw itself, not
    # work anyone would ask an agent to do.
    AppRoute("GET", "/api/ambience", routes_ambience.ambience),
    AppRoute("POST", "/api/ambience", routes_ambience.set_ambience),
    AppRoute("GET", "/api/beta", routes_ambience.beta),
    AppRoute("POST", "/api/beta/{mode_key}/ack", routes_ambience.ack_beta),
    AppRoute("GET", "/api/feedback/options", routes_feedback.options),
    AppRoute("POST", "/api/feedback", routes_feedback.submit),
    AppRoute("POST", "/api/feedback/polish", routes_feedback.polish),
    AppRoute("POST", "/api/feedback/attachments", routes_feedback.attach),
    AppRoute("GET", "/api/consent", routes_consent.pending),
    AppRoute("POST", "/api/consent/{req_id}", routes_consent.resolve),
    # -- dictation (the M11 surface) ------------------------------------------
    # Not a capability of its own on any surface — it is an input method, like
    # paste. Its setup is what the registry names, and that has always belonged
    # to settings (`--install-voice`, `--list-audio-devices`).
    AppRoute("GET", "/api/voice", routes_voice.status, "settings"),
    AppRoute("POST", "/api/voice/offer", routes_voice.offer, "settings"),
    AppRoute("POST", "/api/voice/install", routes_voice.install, "settings"),
    AppRoute("POST", "/api/voice/transcribe", routes_voice.transcribe, "settings"),
)


def build_router(app) -> Router:
    """Materialise :data:`ROUTES` into a Router bound to ``app``."""
    router = Router()
    for route in ROUTES:
        router.add(route.method, route.path, partial(route.handler, app), auth=route.path not in UNAUTHENTICATED)
    return router
