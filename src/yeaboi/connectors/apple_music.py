"""Apple Music — an album or playlist as a source on the desktop's Music page.

No credential, ever: the desktop browses the Music app's own library on this
Mac, plays previews through Apple's public embed player, searches the catalogue
through Apple's public iTunes Search API, and hands a link to the Music app
for full tracks. The one field is where playback happens, and choosing it is
what switches the service on.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

#: Where an Apple Music link plays once it is picked on the Music page.
PLAYBACK_ENV = "APPLE_MUSIC_PLAYBACK"
PLAYBACK_CHOICES = ("desktop", "embed")

CONNECTOR = Connector(
    key="apple_music",
    label="Apple Music",
    family="music",
    section="connections",
    summary="Your albums and playlists as a focus-music source in the desktop app",
    detail=(
        "yeaboi shows an album or playlist you paste through Apple Music's own embedded "
        "player, which plays previews, and hands it to the Music app for full tracks. On a Mac "
        "the desktop browses the Music app's own library — the playlists already on this "
        "machine — and searches the catalogue through Apple's public iTunes Search API, with "
        "30-second previews. It never signs in with your Apple ID, never reads your listening "
        "history, and never writes anything to Apple Music."
    ),
    verify="_verify_apple_music",
    docs_url="https://support.apple.com/music",
    glyph="\U0001f34e",  # 🍎 — the apple
    accent="rgb(250,45,85)",
    fields=(
        ConnectorField(
            env=PLAYBACK_ENV,
            label="Where it plays",
            choices=PLAYBACK_CHOICES,
            default="desktop",
            hint="In the Music app for full tracks, or inside yeaboi for previews",
        ),
    ),
)
