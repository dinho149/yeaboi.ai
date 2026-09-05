"""YouTube Music — a playlist or video as a source on the desktop's Music page.

Paste a link and YouTube's public embed plays it in full inside the desktop,
with no credential. Signing in is optional: a read-only Google sign-in
(``youtube.readonly``) lets the desktop browse your playlists and liked
videos. Google's "Desktop app" clients carry a non-confidential secret; see
``oauth_clients``.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

#: Where a YouTube link plays once it is picked on the Music page.
PLAYBACK_ENV = "YOUTUBE_MUSIC_PLAYBACK"
PLAYBACK_CHOICES = ("embed", "browser")

#: The bring-your-own Google client, and what the sign-in writes.
CLIENT_ID_ENV = "YOUTUBE_MUSIC_CLIENT_ID"
CLIENT_SECRET_ENV = "YOUTUBE_MUSIC_CLIENT_SECRET"  # noqa: S105 — an env NAME, not a credential
REFRESH_TOKEN_ENV = "YOUTUBE_MUSIC_REFRESH_TOKEN"  # noqa: S105
ACCOUNT_ENV = "YOUTUBE_MUSIC_ACCOUNT"

CONNECTOR = Connector(
    key="youtube_music",
    label="YouTube Music",
    family="music",
    section="connections",
    summary="Playlists and videos as a focus-music source in the desktop app",
    detail=(
        "yeaboi plays a playlist or video you paste through YouTube's own embedded player, "
        "in full, inside the desktop app — with no sign-in at all. Signing in is optional and "
        "read-only: it lets the desktop browse your playlists and liked videos. yeaboi never "
        "reads your watch history and never writes anything to YouTube. Until Google verifies "
        "yeaboi's app, its shared client is limited to a hundred testers and one daily quota, so "
        "paste your own client for reliable search."
    ),
    verify="_verify_youtube_music",
    docs_url="https://support.google.com/youtubemusic/",
    glyph="▶️",  # ▶️ — the play mark
    accent="rgb(255,0,0)",
    signin_env=REFRESH_TOKEN_ENV,
    account_env=ACCOUNT_ENV,
    fields=(
        ConnectorField(
            env=PLAYBACK_ENV,
            label="Where it plays",
            choices=PLAYBACK_CHOICES,
            default="embed",
            hint="Inside yeaboi in full, or in the browser",
        ),
        ConnectorField(
            env=CLIENT_ID_ENV,
            label="Your own Google client",
            required=False,
            placeholder="yeaboi's registered app",
            hint="Optional — a Desktop app OAuth client ID from your own Google Cloud project",
            help_url="https://console.cloud.google.com/apis/credentials",
            help_scope=(
                "Create a Desktop app OAuth client with the YouTube Data API enabled. "
                "Needed only when yeaboi's shared client is full or over quota."
            ),
        ),
        ConnectorField(
            env=CLIENT_SECRET_ENV,
            label="Its client secret",
            secret=True,
            required=False,
            hint="Google issues Desktop app clients one; it is not confidential, but it is still yours",
        ),
        ConnectorField(
            env=REFRESH_TOKEN_ENV,
            label="Account",
            secret=True,
            required=False,
            action="signin",
            hint="Written by Sign in on the desktop's Music page; never typed",
        ),
        ConnectorField(
            env=ACCOUNT_ENV,
            label="Signed in as",
            required=False,
            action="signin",
        ),
    ),
)
