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

from yeaboi.app import routes_analysis, routes_chat, routes_meta, routes_settings, routes_standup
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
)


def build_router(app) -> Router:
    """Materialise :data:`ROUTES` into a Router bound to ``app``."""
    router = Router()
    for route in ROUTES:
        router.add(route.method, route.path, partial(route.handler, app), auth=route.path not in UNAUTHENTICATED)
    return router
