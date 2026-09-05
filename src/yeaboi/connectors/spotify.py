"""Spotify — a playlist as a source on the desktop's Music page.

Two ways in, both optional beyond the one choice that switches it on: paste a
link and it plays through Spotify's public embed player (previews) or the
Spotify app (full tracks), with no credential at all; or sign in, and the
desktop can browse your playlists, liked songs, albums and recently played.
The sign-in is Authorization Code + PKCE against yeaboi's registered app or
one of your own, and what it writes here is a refresh token and a display
name — never a password.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

#: Where a Spotify link plays once it is picked on the Music page.
PLAYBACK_ENV = "SPOTIFY_PLAYBACK"
PLAYBACK_CHOICES = ("desktop", "embed")

#: The bring-your-own app, and what the sign-in writes.
CLIENT_ID_ENV = "SPOTIFY_CLIENT_ID"
REFRESH_TOKEN_ENV = "SPOTIFY_REFRESH_TOKEN"  # noqa: S105 — an env NAME, not a credential
ACCOUNT_ENV = "SPOTIFY_ACCOUNT"

CONNECTOR = Connector(
    key="spotify",
    label="Spotify",
    family="music",
    section="connections",
    summary="Your playlists as a focus-music source in the desktop app",
    detail=(
        "yeaboi shows a playlist you paste through Spotify's own embedded player, which "
        "plays previews, and hands it to the Spotify app for full tracks — with no sign-in at "
        "all. Signing in is optional: it lets the desktop browse your playlists, liked songs, "
        "albums and recently played, read-only. yeaboi never creates, follows, likes or edits "
        "anything; pressing Play sends a play command to your Spotify app (Premium only), and "
        "that is the only thing it ever changes. Spotify's developer terms cap an unapproved "
        "app at five sign-ins, so a 'not allowlisted' message means paste your own client ID."
    ),
    verify="_verify_spotify",
    docs_url="https://support.spotify.com/",
    glyph="\U0001f3a7",  # 🎧 — headphones
    accent="rgb(30,215,96)",
    signin_env=REFRESH_TOKEN_ENV,
    account_env=ACCOUNT_ENV,
    fields=(
        ConnectorField(
            env=PLAYBACK_ENV,
            label="Where it plays",
            choices=PLAYBACK_CHOICES,
            default="desktop",
            hint="In the Spotify app for full tracks, or inside yeaboi for previews",
        ),
        ConnectorField(
            env=CLIENT_ID_ENV,
            label="Your own Spotify app",
            required=False,
            placeholder="yeaboi's registered app",
            hint="Optional — a client ID from your own Spotify developer app",
            help_url="https://developer.spotify.com/dashboard",
            help_scope=(
                "Create an app and add http://127.0.0.1:8643/callback/spotify as its Redirect URI. "
                "Needed only past the five sign-ins yeaboi's own app allows."
            ),
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
