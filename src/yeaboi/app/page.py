"""The application shell document.

One page for every in-app route. The client router owns what is drawn; the
server's job is to hand over the same shell whatever the path, plus the boot
payload saying who is asking.

Server-rendering the signed-in user rather than making the client fetch it is
worth one query: without it every cold load flashes the sign-in form before the
session resolves, which is the single most obvious way an app looks broken.
"""

from __future__ import annotations

from yeaboi.app.store import AppStore
from yeaboi.web.assets import render_page


def render_app_page(store: AppStore, user_id: str | None) -> str:
    """The shell, booted with the current user (or ``None`` when signed out)."""
    user = store.user(user_id) if user_id else None
    payload = {
        "user": {"id": user.id, "email": user.email, "name": user.name} if user else None,
    }
    return render_page(
        bundle="app",
        title="yeaboi",
        data=payload,
        # The shell is an application, not a document: there is nothing
        # meaningful to render without the bundle, so the noscript says so
        # rather than pretending to be a fallback page.
        body='<noscript>yeaboi needs JavaScript enabled.</noscript>',
        head='<meta name="robots" content="noindex, nofollow">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
    )
