"""Background music via ffplay — optional, auto-detected headless stream player.

This module lets users play focus music while planning. It shells out to
**ffplay** (ffmpeg's bundled player), and is modelled directly on
:mod:`yeaboi.voice`: an optional, provider-agnostic helper that talks to an
external binary.

# See docs: "Music (ffplay)" — like voice input, background music does NOT go
# through the LangGraph agent or the get_llm() provider factory. It is a pure
# terminal-UX helper.

Design notes / architectural decisions:
- **Why ffplay, not a TUI player.** Background music has to play *headlessly*
  alongside our own full-screen TUI. Interactive terminal players (e.g. cliamp,
  which this module originally targeted) refuse to start without a controlling TTY
  and would fight us for the terminal. ``ffplay -nodisp`` has no UI of its own and
  reads a stream URL directly, so it plays in the background and never touches the
  terminal. ffmpeg is ubiquitous and frequently already installed.
- **Optional and auto-detected.** ffplay is a binary, not a Python package, so we
  never import or bundle it. :func:`is_music_available` just checks
  ``shutil.which("ffplay")`` — cheap enough to call on every screen render,
  mirroring :func:`yeaboi.voice.is_voice_available`. If the binary is absent
  every entry point below is a graceful no-op.
- **One long-lived process per stream.** We keep a handle to a single ffplay
  subprocess playing the current channel. ffplay exposes no IPC, so transport is
  done on the process itself: pause/resume suspend and continue it with
  ``SIGSTOP`` / ``SIGCONT`` (a genuine pause of audio output), and switching
  channel or stopping terminates and respawns it.
- **Channels = radio streams.** ffplay plays a stream URL directly, so a
  "channel" is just an entry in :data:`CHANNELS`. Switching channel respawns the
  player on the next stream URL.
- **Never raises into the TUI.** Every subprocess/signal call is wrapped; a
  missing or broken ffplay logs a warning and leaves music off. Music must never
  break planning.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import signal
import subprocess
import time

logger = logging.getLogger(__name__)

# The headless player we drive. ffplay ships with ffmpeg and, with -nodisp, plays
# a stream URL with no window and no terminal of its own.
_PLAYER = "ffplay"

# ffplay is spawned instantly but a remote radio stream takes a moment to connect
# and buffer before any audio actually comes out. During that gap the status bar
# would otherwise animate a full equalizer with silence, which reads as "broken".
# For this grace window after a fresh spawn we report a "connecting" display state
# (see :func:`is_connecting`) so the bar shows a buffering indicator instead. It's a
# time-based heuristic — ffplay exposes no IPC to signal first-audio — tuned to the
# typical somafm/stream connect time. Pause→resume (SIGCONT) resumes already-buffered
# audio instantly and deliberately does NOT re-enter this state.
_CONNECT_GRACE_SECONDS = 2.5

# SIGSTOP/SIGCONT give us a true pause without any player-side IPC. They are
# POSIX-only; on a platform without them (Windows) pause degrades to stop.
_CAN_SUSPEND = hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")

# Built-in "channels": stable public internet-radio streams suited to focus work.
# Kept intentionally small; ffplay plays each URL directly.
CHANNELS: tuple[dict[str, str], ...] = (
    {"name": "Lofi", "url": "https://ice1.somafm.com/groovesalad-128-mp3"},
    {"name": "Jazz", "url": "https://ice1.somafm.com/sonicuniverse-128-mp3"},
    {"name": "Classical", "url": "https://stream.srg-ssr.ch/m/rsc_de/mp3_128"},
    {"name": "Ambient", "url": "https://ice1.somafm.com/dronezone-128-mp3"},
)


def _player_command(url: str) -> list[str]:
    """Return the ffplay argv for headless playback of ``url``.

    ``-nodisp`` suppresses the video/SDL window (the key to running headlessly),
    ``-autoexit`` ends the process when the stream does, and ``-loglevel quiet``
    ``-nostats`` silence ffplay's banner/progress so nothing leaks onto our TUI.
    """
    return [_PLAYER, "-nodisp", "-autoexit", "-loglevel", "quiet", "-nostats", url]


class _State:
    """Mutable in-process music state (there is one music player per process)."""

    def __init__(self) -> None:
        # "stopped" (no audio), "playing", or "paused".
        self.status: str = "stopped"
        self.channel_idx: int = 0
        # Handle to the running ``ffplay`` subprocess, or None.
        self.daemon: subprocess.Popen | None = None
        # ``time.monotonic()`` at the last fresh spawn, for the connect/buffer grace
        # window (see :data:`_CONNECT_GRACE_SECONDS` and :func:`is_connecting`).
        self.started_at: float = 0.0
        # True while playback is suspended for a voice recording, so we only auto
        # resume music that *we* paused (not music the user paused themselves).
        self._paused_for_voice: bool = False
        self._initialised: bool = False
        # Last user-relevant failure (e.g. the player exited on its own), surfaced
        # by the status bar so a silent process death doesn't masquerade as playback.
        self.last_error: str = ""


_state = _State()


def _init_state() -> None:
    """Load the persisted channel preference once, lazily on first use."""
    if _state._initialised:
        return
    _state._initialised = True
    try:
        from yeaboi.config import get_music_channel

        idx = get_music_channel()
        if 0 <= idx < len(CHANNELS):
            _state.channel_idx = idx
    except Exception:  # noqa: BLE001 - config is best-effort; default channel is fine
        logger.debug("Could not load music channel preference", exc_info=True)


def is_music_available() -> tuple[bool, str]:
    """Return (available, reason) describing whether background music can be used.

    Music needs the optional ``ffplay`` binary (part of ffmpeg) on PATH. ``reason``
    is empty when available, otherwise a short human-readable install hint for the
    UI. Mirrors :func:`yeaboi.voice.is_voice_available` so callers/tests feel
    familiar.
    """
    if shutil.which(_PLAYER) is None:
        return False, "Install ffmpeg to enable music (brew install ffmpeg)"
    return True, ""


# ── Introspection (read by the status bar) ────────────────────────────────────


def _reconcile_status() -> None:
    """Fall back to a truthful "stopped" state when the player has died on its own.

    ``subprocess.Popen`` only reports that the *spawn* succeeded — not that ffplay
    keeps running. ffplay can exit on its own (an unreachable/failed stream URL, a
    codec/audio-device problem) while :func:`_start_channel` has already
    optimistically set the status to "playing". Because we discard stderr, that
    death is otherwise invisible and the status bar animates a phantom equalizer
    with no audio. This is called from every read below, so a crash is reflected
    within roughly one render frame — the "verify shortly after launch" check, done
    lazily instead of blocking the toggle.
    """
    if _state.status in ("playing", "paused") and _state.daemon is not None and _state.daemon.poll() is not None:
        rc = _state.daemon.returncode
        _state.daemon = None
        _state.status = "stopped"
        _state._paused_for_voice = False
        _state.last_error = "music stopped — stream unavailable, ^P to retry"
        logger.warning("Music player exited on its own (rc=%s); reverting to stopped", rc)


def status() -> str:
    """Return the current player status: "stopped", "playing", or "paused"."""
    _reconcile_status()
    return _state.status


def is_playing() -> bool:
    """Return True when audio is actively playing (not paused/stopped)."""
    return status() == "playing"


def is_connecting() -> bool:
    """Return True during the brief connect/buffer window after a fresh start.

    ffplay is spawned and marked "playing" instantly, but a remote stream needs a
    moment to connect and buffer before audio is audible. For
    :data:`_CONNECT_GRACE_SECONDS` after a fresh spawn we're "playing" but not yet
    audible; the status bar shows a buffering indicator over this window instead of
    the equalizer so the silence doesn't look broken. Returns False once the window
    elapses, when paused/stopped, or if the player has already died (reconciled to
    "stopped"). This is a derived *display* state — :func:`status` stays canonical
    ("stopped"/"playing"/"paused").
    """
    return status() == "playing" and (time.monotonic() - _state.started_at) < _CONNECT_GRACE_SECONDS


def last_error() -> str:
    """Return the last user-relevant music failure message, or "" if none.

    Set when a spawned ffplay process dies unexpectedly (see :func:`_reconcile_status`)
    and cleared on the next successful start. Read by the status bar to explain why
    music isn't playing despite ffplay being installed.
    """
    return _state.last_error


def current_channel_name() -> str:
    """Return the name of the currently selected channel."""
    _init_state()
    return CHANNELS[_state.channel_idx]["name"]


# ── Subprocess helpers ────────────────────────────────────────────────────────


def _signal_daemon(sig: int) -> bool:
    """Send ``sig`` to the running player process. Never raises.

    Used for pause (``SIGSTOP``) and resume (``SIGCONT``) — ffplay has no IPC, so
    transport is done on the process itself. Returns True when the signal was
    delivered to a live player, False otherwise (no player, dead player, or an
    OS-level failure). Failures are logged and swallowed so the TUI is never
    disrupted.
    """
    if not (_CAN_SUSPEND and _daemon_alive()):
        return False
    try:
        os.kill(_state.daemon.pid, sig)
        return True
    except Exception:  # noqa: BLE001 - music is best-effort; surface as a warning only
        logger.warning("Failed to signal music player (sig=%s)", sig, exc_info=True)
        return False


def _daemon_alive() -> bool:
    """Return True if the player subprocess is running."""
    return _state.daemon is not None and _state.daemon.poll() is None


def _stop_daemon() -> None:
    """Terminate the running player (if any) and clear the handle."""
    if _state.daemon is None:
        return
    try:
        if _state.daemon.poll() is None:
            _state.daemon.terminate()
            try:
                _state.daemon.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                _state.daemon.kill()
        logger.info("Music daemon stopped")
    except Exception:  # noqa: BLE001 - best-effort teardown
        logger.warning("Failed to stop music daemon", exc_info=True)
    finally:
        _state.daemon = None


def _start_channel() -> bool:
    """Spawn a fresh ffplay process playing the currently selected channel.

    Any existing player is torn down first so we never leave an orphan or play two
    streams at once. Returns True when the player was spawned.
    """
    _init_state()
    _stop_daemon()
    channel = CHANNELS[_state.channel_idx]
    try:
        _state.daemon = subprocess.Popen(
            _player_command(channel["url"]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        _state.status = "playing"
        _state.started_at = time.monotonic()  # begin the connect/buffer grace window
        _state.last_error = ""  # a fresh start clears any prior crash notice
        logger.info("Music playing channel %s", channel["name"])
        return True
    except Exception:  # noqa: BLE001 - music is best-effort
        logger.warning("Failed to start music player for %s", channel["name"], exc_info=True)
        _state.daemon = None
        _state.status = "stopped"
        return False


def _nudge() -> None:
    """Force an immediate redraw of the persistent status bar, if one is active."""
    try:
        from yeaboi.ui.shared._music_bar import nudge_music_bar

        nudge_music_bar()
    except Exception:  # noqa: BLE001 - the bar is optional (e.g. headless/tests)
        logger.debug("Music bar nudge skipped", exc_info=True)


# ── Public controls (bound to Ctrl+P / Ctrl+O and voice hooks) ────────────────


def toggle() -> None:
    """Play/pause the music (Ctrl+P). Starts the current channel if stopped."""
    available, reason = is_music_available()
    if not available:
        logger.info("Music toggle ignored: %s", reason)
        return
    _init_state()
    if _state.status == "playing":
        if _signal_daemon(signal.SIGSTOP if _CAN_SUSPEND else 0):
            _state.status = "paused"
            logger.info("Music paused")
        else:
            # No POSIX suspend (e.g. Windows) — stop the player so "pause" still
            # silences audio rather than doing nothing.
            _stop_daemon()
            _state.status = "stopped"
            logger.info("Music stopped (suspend unavailable)")
    elif _state.status == "paused" and _daemon_alive():
        if _signal_daemon(signal.SIGCONT):
            _state.status = "playing"
            logger.info("Music resumed")
    else:
        _start_channel()
    _persist_enabled(_state.status != "stopped")
    _nudge()


def cycle_channel() -> None:
    """Switch to the next channel (Ctrl+O), wrapping around.

    If music is currently playing/paused the daemon respawns on the new stream and
    starts playing; if stopped we just remember the new selection.
    """
    available, reason = is_music_available()
    if not available:
        logger.info("Music channel switch ignored: %s", reason)
        return
    _init_state()
    _state.channel_idx = (_state.channel_idx + 1) % len(CHANNELS)
    _persist_channel(_state.channel_idx)
    logger.info("Music channel -> %s", CHANNELS[_state.channel_idx]["name"])
    if _state.status in ("playing", "paused"):
        _start_channel()
    _nudge()


def pause_for_voice() -> None:
    """Pause playback while a voice note is recorded; remember to resume it."""
    if _state.status == "playing":
        _state._paused_for_voice = _signal_daemon(signal.SIGSTOP) if _CAN_SUSPEND else False
        if _state._paused_for_voice:
            _state.status = "paused"
            logger.info("Music paused for voice recording")


def resume_after_voice() -> None:
    """Resume playback that :func:`pause_for_voice` suspended."""
    if _state._paused_for_voice:
        _state._paused_for_voice = False
        if _signal_daemon(signal.SIGCONT):
            _state.status = "playing"
            logger.info("Music resumed after voice recording")


def shutdown() -> None:
    """Stop the daemon on app exit. Idempotent; safe to call more than once."""
    _stop_daemon()
    _state.status = "stopped"


# Backstop so a crash or a missed cli.py finally still cleans up the daemon.
atexit.register(shutdown)


# ── Preference persistence (mirrors config.set_tips_enabled) ──────────────────


def _persist_enabled(enabled: bool) -> None:
    try:
        from yeaboi.config import set_music_enabled

        set_music_enabled(enabled)
    except Exception:  # noqa: BLE001 - preference persistence is best-effort
        logger.debug("Could not persist music-enabled preference", exc_info=True)


def _persist_channel(idx: int) -> None:
    try:
        from yeaboi.config import set_music_channel

        set_music_channel(idx)
    except Exception:  # noqa: BLE001 - preference persistence is best-effort
        logger.debug("Could not persist music channel preference", exc_info=True)
