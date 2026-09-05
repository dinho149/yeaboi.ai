"""Ambience — the duck's vocabulary, the music catalogue, the idle threshold.

The parts of yeaboi that are not a feature: what the duck is allowed to say
when something finishes, which stations the music player knows, how long a
screen sits untouched before the ducks take it over, and whether the desktop
pet is on.

None of that is terminal-specific, but all of it used to live in ``ui/shared``.
The two surfaces render it about as differently as two surfaces can — a Rich
speech bubble against a DOM one, an ``ffplay`` subprocess against an ``<audio>``
element — so what belongs here is the vocabulary and the preferences, and what
stays with each surface is the drawing.

Preferences are ``.env`` values read through :mod:`yeaboi.config`, so a duck
muted in the terminal is muted in the app.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The reaction vocabulary — one short line per completion event, so every
# surface says the same thing when the same thing happens. Dynamic lines
# (counts, filenames) are passed to the voice directly. Kept ≤ 40 chars by a
# unit test: a quip that wraps is a quip that gets clipped in the bubble.
DUCK_QUIPS: dict[str, str] = {
    "standup_done": "Standup's up!",
    "report_done": "Report's ready!",
    "roadmap_done": "Plan's plotted!",
    "export_done": "Saved it!",
    "link_ready": "Link's live!",
    "sync_done": "Synced!",
    "actions_done": "Actions drafted!",
    "analysis_done": "Team mapped!",
    "poker_done": "Points dealt!",
    "artifact_done": "Done and dusted!",
    "anonymize_done": "Scrubbed clean!",
}

#: How long a surface waits on a person before the screensaver takes over.
IDLE_SECONDS = 5 * 60

# The screensaver catalogue: key → the name a picker shows. Like the music
# stations, this is served as a catalogue *and* a preference, because only the
# surface drawing it knows how — the desktop renders all of these on a canvas,
# the terminal draws its ducks for any of them and honours "off".
SAVER_STYLES: dict[str, str] = {
    "duck-yard": "Duck Yard",
    "constellation": "Constellation",
    "ricochet": "Ricochet",
    "aurora": "Aurora",
    "shuffle": "Shuffle",
    "off": "Off",
}

#: What an unset or unrecognised SAVER_STYLE behaves as.
DEFAULT_SAVER_STYLE = "duck-yard"


def music_channels() -> list[dict[str, str]]:
    """The station list, as data.

    The terminal hands these URLs to ``ffplay``; the desktop hands them to an
    ``<audio>`` element and needs no binary at all. Same stations either way.
    """
    from yeaboi.music import CHANNELS

    return [dict(channel) for channel in CHANNELS]


#: The catalogue connectors that are music sources on the desktop, in the order
#: the Music page lists them after the built-in radio.
MUSIC_SERVICE_KEYS: tuple[str, ...] = ("spotify", "apple_music", "youtube_music")


def _client_kind(key: str) -> str:
    from yeaboi.connectors import oauth_clients

    client = oauth_clients.resolve(key)
    return "none" if client is None else ("own" if client.own else "builtin")


def music_services() -> list[dict]:
    """The streaming services, and whether each is switched on in the catalogue.

    A service is on when its connector is connected, which means its "where it
    plays" choice has been saved; signing in is a separate, optional step, and
    ``signed_in``/``account`` say where it stands. The choice travels too: it
    is the first field, and it is never a secret.
    """
    import os

    from yeaboi.connectors import registry

    services = []
    for key in MUSIC_SERVICE_KEYS:
        connector = registry.by_key(key)
        if connector is None:
            continue
        field = connector.fields[0]
        playback = os.environ.get(field.env, "").strip() or field.default
        services.append(
            {
                "key": key,
                "label": connector.label,
                "connected": registry.is_connected(connector),
                "playback": playback if playback in field.choices else field.default,
                "can_sign_in": connector.can_sign_in,
                "signed_in": bool(os.environ.get(connector.signin_env, "").strip()) if connector.can_sign_in else False,
                "account": os.environ.get(connector.account_env, "").strip() if connector.account_env else "",
                # Which OAuth app a sign-in would use: the user's own, yeaboi's
                # built-in, or none — so the page can ask for one before a click.
                "client": _client_kind(key) if connector.can_sign_in else "none",
            }
        )
    return services


def state() -> dict:
    """Every ambience preference and catalogue, in one read."""
    from yeaboi import config

    channels = music_channels()
    channel = config.get_music_channel()
    style = config.get_saver_style()
    return {
        "duck": {"enabled": config.is_duck_enabled(), "quips": dict(DUCK_QUIPS)},
        "music": {
            "channels": channels,
            "channel": channel if 0 <= channel < len(channels) else 0,
            "enabled": config.is_music_enabled(),
            "services": music_services(),
        },
        "saver": {
            "idle_seconds": IDLE_SECONDS,
            "style": style if style in SAVER_STYLES else DEFAULT_SAVER_STYLE,
            "styles": dict(SAVER_STYLES),
        },
        "pet": {"enabled": config.is_pet_enabled()},
    }


def apply(changes: dict) -> dict:
    """Persist the recognised preferences in ``changes`` and return the new state.

    An unknown key is a caller bug and raises; a known key with an unusable
    value raises too, so a bad channel index never silently becomes station 0.
    """
    from yeaboi import config

    known = {"duck_enabled", "music_enabled", "music_channel", "pet_enabled", "saver_style"}
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValueError(f"unknown ambience setting(s): {', '.join(unknown)} — one of {', '.join(sorted(known))}")

    if "duck_enabled" in changes:
        config.set_duck_enabled(_flag(changes["duck_enabled"], "duck_enabled"))
    if "music_enabled" in changes:
        config.set_music_enabled(_flag(changes["music_enabled"], "music_enabled"))
    if "music_channel" in changes:
        config.set_music_channel(_channel(changes["music_channel"]))
    if "pet_enabled" in changes:
        config.set_pet_enabled(_flag(changes["pet_enabled"], "pet_enabled"))
    if "saver_style" in changes:
        config.set_saver_style(_saver_style(changes["saver_style"]))
    logger.info("ambience updated: %s", ", ".join(sorted(changes)))
    return state()


def _flag(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false, got {value!r}")
    return value


def _saver_style(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"saver_style must be a string, got {value!r}")
    style = value.strip().lower()
    if style not in SAVER_STYLES:
        raise ValueError(f"unknown saver_style {value!r} — one of {', '.join(sorted(SAVER_STYLES))}")
    return style


def _channel(value: object) -> int:
    channels = music_channels()
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"music_channel must be an integer, got {value!r}")
    if not 0 <= value < len(channels):
        raise ValueError(f"music_channel {value} is out of range — 0..{len(channels) - 1}")
    return value
