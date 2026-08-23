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

from yeaboi.app import routes_meta, routes_settings
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
)


def build_router(app) -> Router:
    """Materialise :data:`ROUTES` into a Router bound to ``app``."""
    router = Router()
    for route in ROUTES:
        router.add(route.method, route.path, partial(route.handler, app), auth=route.path not in UNAUTHENTICATED)
    return router
