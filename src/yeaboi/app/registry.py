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
    routes_analysis,
    routes_boards,
    routes_chat,
    routes_meta,
    routes_settings,
    routes_share,
    routes_standup,
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
    AppRoute("POST", "/api/settings/signin/start", routes_settings.signin_start, "settings"),
    AppRoute("GET", "/api/settings/signin", routes_settings.signin_status, "settings"),
    AppRoute("POST", "/api/settings/signin/code", routes_settings.signin_code, "settings"),
    AppRoute("POST", "/api/settings/signin/cancel", routes_settings.signin_cancel, "settings"),
    # -- the planning chat (capability "planning" — the M5 surface) ----------
    AppRoute("POST", "/api/chat/sessions", routes_chat.create, "planning"),
    AppRoute("GET", "/api/chat/sessions/{project_id}", routes_chat.get, "planning"),
    AppRoute("POST", "/api/chat/sessions/{project_id}/send", routes_chat.send, "planning"),
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
    AppRoute("GET", "/api/artifacts/{kind}/edits", routes_share.artifact_edits, "artifact-editing"),
    AppRoute("POST", "/api/anonymize", routes_share.anonymize, "anonymize"),
)


def build_router(app) -> Router:
    """Materialise :data:`ROUTES` into a Router bound to ``app``."""
    router = Router()
    for route in ROUTES:
        router.add(route.method, route.path, partial(route.handler, app), auth=route.path not in UNAUTHENTICATED)
    return router
