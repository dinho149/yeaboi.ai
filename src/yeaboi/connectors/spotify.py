"""Spotify — a playlist as a source on the desktop's Music page.

No credential: the desktop browses through Spotify's public embed player and
hands a playlist to the Spotify app for full tracks. The one field is where
playback happens, and choosing it is what switches the service on.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

#: Where a Spotify link plays once it is picked on the Music page.
PLAYBACK_ENV = "SPOTIFY_PLAYBACK"
PLAYBACK_CHOICES = ("desktop", "embed")

CONNECTOR = Connector(
    key="spotify",
    label="Spotify",
    family="music",
    section="connections",
    summary="Your playlists as a focus-music source in the desktop app",
    detail=(
        "yeaboi shows a playlist you paste through Spotify's own embedded player, which "
        "plays previews, and hands it to the Spotify app for full tracks. It never signs in "
        "to your account, never reads your library or listening history, and never writes "
        "anything to Spotify."
    ),
    verify="_verify_spotify",
    docs_url="https://support.spotify.com/",
    glyph="\U0001f3a7",  # 🎧 — headphones
    accent="rgb(30,215,96)",
    fields=(
        ConnectorField(
            env=PLAYBACK_ENV,
            label="Where it plays",
            choices=PLAYBACK_CHOICES,
            default="desktop",
            hint="In the Spotify app for full tracks, or inside yeaboi for previews",
        ),
    ),
)
