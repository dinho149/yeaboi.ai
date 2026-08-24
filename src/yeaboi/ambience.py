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


def music_channels() -> list[dict[str, str]]:
    """The station list, as data.

    The terminal hands these URLs to ``ffplay``; the desktop hands them to an
    ``<audio>`` element and needs no binary at all. Same stations either way.
    """
    from yeaboi.music import CHANNELS

    return [dict(channel) for channel in CHANNELS]


def state() -> dict:
    """Every ambience preference and catalogue, in one read."""
    from yeaboi import config

    channels = music_channels()
    channel = config.get_music_channel()
    return {
        "duck": {"enabled": config.is_duck_enabled(), "quips": dict(DUCK_QUIPS)},
        "music": {
            "channels": channels,
            "channel": channel if 0 <= channel < len(channels) else 0,
            "enabled": config.is_music_enabled(),
        },
        "saver": {"idle_seconds": IDLE_SECONDS},
        "pet": {"enabled": config.is_pet_enabled()},
    }


def apply(changes: dict) -> dict:
    """Persist the recognised preferences in ``changes`` and return the new state.

    An unknown key is a caller bug and raises; a known key with an unusable
    value raises too, so a bad channel index never silently becomes station 0.
    """
    from yeaboi import config

    known = {"duck_enabled", "music_enabled", "music_channel", "pet_enabled"}
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
    logger.info("ambience updated: %s", ", ".join(sorted(changes)))
    return state()


def _flag(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false, got {value!r}")
    return value


def _channel(value: object) -> int:
    channels = music_channels()
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"music_channel must be an integer, got {value!r}")
    if not 0 <= value < len(channels):
        raise ValueError(f"music_channel {value} is out of range — 0..{len(channels) - 1}")
    return value
