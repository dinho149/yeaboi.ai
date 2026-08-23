"""Native settings routes — the desktop's door to ~/.yeaboi/.env.

Thin adapters over :mod:`yeaboi.settings.engine`: the engine masks secrets and
allowlists writes, these handlers only parse the request shape. The one piece
of state owned here is the subscription sign-in session (``app.signin``) —
a running ``claude setup-token`` is a process, not a value, so it lives on the
server and is driven a poll at a time by the renderer.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def get_settings(app, request: Request) -> Response:
    """The full field inventory, secrets masked. Never carries a raw credential."""
    from yeaboi.settings import engine

    return json_response(to_jsonable(engine.get_settings()))


def providers(app, request: Request) -> Response:
    """The setup wizard's provider catalog (cards, auth modes, token help)."""
    from yeaboi.settings import engine

    return json_response(engine.provider_catalog())


def set_setting(app, request: Request) -> Response:
    """``POST /api/settings/set`` — one allowlisted ``{key, value}`` write."""
    payload = request.json()
    key, value = payload.get("key"), payload.get("value", "")
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    from yeaboi.settings import engine

    return json_response(to_jsonable(engine.set_setting(key, value)))


def allowed_paths(app, request: Request) -> Response:
    """``POST /api/settings/allowed-paths`` — replace the sandbox whitelist."""
    payload = request.json()
    from yeaboi.settings import engine

    return json_response(to_jsonable(engine.set_allowed_paths(payload.get("paths", []))))


def data_dir(app, request: Request) -> Response:
    """``POST /api/settings/data-dir`` — set YEABOI_HOME, optionally moving the tree."""
    payload = request.json()
    value = payload.get("value", "")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    from yeaboi.settings import engine

    return json_response(to_jsonable(engine.set_data_dir(value, move=bool(payload.get("move")))))


def provider_verify(app, request: Request) -> Response:
    """``POST /api/settings/provider/verify`` — live credential (+ model) check."""
    payload = request.json()
    from yeaboi.settings import engine

    return json_response(
        engine.verify_provider(
            _required_str(payload, "provider"),
            str(payload.get("credential", "")),
            model=str(payload.get("model", "")),
        )
    )


def provider_models(app, request: Request) -> Response:
    """``POST /api/settings/provider/models`` — live model discovery + presets."""
    payload = request.json()
    from yeaboi.settings import engine

    return json_response(engine.discover_models(_required_str(payload, "provider"), str(payload.get("credential", ""))))


def _required_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


# -- subscription sign-in ----------------------------------------------------
# One session at a time, held on the app. The token never appears in any
# response; on completion it is persisted straight into config and the status
# only says so.


def signin_start(app, request: Request) -> Response:
    """``POST /api/settings/signin/start`` — spawn ``claude setup-token``."""
    from yeaboi.claude_auth import SubscriptionSignIn

    with app.signin_lock:
        if app.signin is not None:
            app.signin.cancel()
            app.signin = None
        session = SubscriptionSignIn()
        if not session.start():
            logger.warning("settings: sign-in could not start")
            return json_response({"started": False, "message": session.message})
        app.signin = session
    logger.info("settings: sign-in started")
    return json_response({"started": True, "message": ""})


def signin_status(app, request: Request) -> Response:
    """``GET /api/settings/signin`` — poll the running sign-in.

    On the poll that first sees a token, the token is persisted (subscription
    auth mode included) before the status is reported — so ``saved: true``
    means the credential is already on disk.
    """
    with app.signin_lock:
        session = app.signin
        if session is None:
            return json_response({"active": False})
        session.poll()
        saved = False
        if session.token and not getattr(session, "_persisted", False):
            from yeaboi.config import apply_config_value

            apply_config_value("CLAUDE_CODE_OAUTH_TOKEN", session.token)
            apply_config_value("ANTHROPIC_AUTH_MODE", "subscription")
            session._persisted = True
            logger.info("settings: subscription token persisted")
        if getattr(session, "_persisted", False):
            saved = True
        return json_response(
            {
                "active": True,
                "url": session.url,
                "awaiting_code": session.awaiting_code,
                "done": session.done,
                "ok": bool(session.token),
                "saved": saved,
                "message": session.message if session.done else "",
            }
        )


def signin_code(app, request: Request) -> Response:
    """``POST /api/settings/signin/code`` — submit the pasted authorization code."""
    payload = request.json()
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    with app.signin_lock:
        if app.signin is None:
            raise HTTPError(404, "no sign-in in progress")
        app.signin.send_code(code)
    logger.info("settings: sign-in code submitted")
    return json_response({"ok": True})


def signin_cancel(app, request: Request) -> Response:
    """``POST /api/settings/signin/cancel`` — stop and discard the session."""
    with app.signin_lock:
        session, app.signin = app.signin, None
    if session is not None:
        session.cancel()
        logger.info("settings: sign-in cancelled")
    return json_response({"ok": True})
