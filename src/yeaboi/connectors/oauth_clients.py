"""The OAuth apps yeaboi is registered as, and the user's own in their place.

Spotify and Google each know yeaboi by a client ID. Both are public clients:
the code exchange is bound by PKCE, and a client secret grants nothing on its
own. Google still issues its "Desktop app" clients a ``client_secret`` and says
in its own docs that an installed app cannot keep it confidential — so it is a
shipped constant here, never a user secret, and never logged.

The built-in apps have limits a user may hit — Spotify's development mode
allows five sign-ins; Google caps an unverified app at a hundred testers and
shares one daily quota across everyone — so each connector carries a
bring-your-own client field that takes precedence when set. Stdlib-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: yeaboi's registered apps. Blank until the registrations exist; a blank
#: built-in makes sign-in fail with a message that names the BYO field.
SPOTIFY_CLIENT_ID = ""
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""

#: The bring-your-own override fields, by connector key.
CLIENT_ID_ENVS: dict[str, str] = {"spotify": "SPOTIFY_CLIENT_ID", "youtube_music": "YOUTUBE_MUSIC_CLIENT_ID"}
CLIENT_SECRET_ENVS: dict[str, str] = {"youtube_music": "YOUTUBE_MUSIC_CLIENT_SECRET"}

_BUILTIN: dict[str, tuple[str, str]] = {
    "spotify": (SPOTIFY_CLIENT_ID, ""),
    "youtube_music": (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
}


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    #: True when the user's own app is in force rather than yeaboi's.
    own: bool


def resolve(key: str) -> OAuthClient | None:
    """The client to sign in with: the user's own when set, else yeaboi's.

    ``None`` when neither exists — the caller turns that into the message
    that names the field to fill in.
    """
    own_id = os.environ.get(CLIENT_ID_ENVS.get(key, ""), "").strip()
    if own_id:
        own_secret = os.environ.get(CLIENT_SECRET_ENVS.get(key, ""), "").strip()
        return OAuthClient(own_id, own_secret, own=True)
    builtin_id, builtin_secret = _BUILTIN.get(key, ("", ""))
    if not builtin_id:
        return None
    return OAuthClient(builtin_id, builtin_secret, own=False)
