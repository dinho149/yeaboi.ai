"""YouTube Music — a playlist or video as a source on the desktop's Music page.

No credential: YouTube's public embed plays in full inside the desktop app.
The one field is where playback happens, and choosing it is what switches the
service on.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

#: Where a YouTube link plays once it is picked on the Music page.
PLAYBACK_ENV = "YOUTUBE_MUSIC_PLAYBACK"
PLAYBACK_CHOICES = ("embed", "browser")

CONNECTOR = Connector(
    key="youtube_music",
    label="YouTube Music",
    family="music",
    section="connections",
    summary="Playlists and videos as a focus-music source in the desktop app",
    detail=(
        "yeaboi plays a playlist or video you paste through YouTube's own embedded player, "
        "in full, inside the desktop app. It never signs in to your Google account, never reads "
        "your subscriptions or watch history, and never writes anything to YouTube."
    ),
    verify="_verify_youtube_music",
    docs_url="https://support.google.com/youtubemusic/",
    glyph="▶️",  # ▶️ — the play mark
    accent="rgb(255,0,0)",
    fields=(
        ConnectorField(
            env=PLAYBACK_ENV,
            label="Where it plays",
            choices=PLAYBACK_CHOICES,
            default="embed",
            hint="Inside yeaboi in full, or in the browser",
        ),
    ),
)
