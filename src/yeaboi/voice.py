"""Voice input — record from the microphone and transcribe locally (offline).

This module lets users *speak* their answers instead of typing them. It is used
by the TUI text-entry loops (project description, intake question answers, and
the artifact editor) which trigger recording on Ctrl+R.

# See docs: "Voice Input" — voice is an optional, provider-agnostic helper.
# It does NOT go through the LangGraph agent or the get_llm() provider factory.

Design notes / architectural decisions:
- **Local, provider-agnostic transcription.** Speech-to-text runs on-device via
  `faster-whisper` (a CTranslate2 Whisper implementation). This works no matter
  which LLM_PROVIDER (Anthropic/Bedrock/OpenAI/Google) drives the planning
  agent, and needs **no API key** — Anthropic and Bedrock have no speech-to-text
  endpoint, so a cloud STT would have forced an OpenAI key on everyone.
- **Lazy imports.** Both heavy dependencies (`sounddevice` for mic capture and
  `faster_whisper` for transcription) are imported *inside* functions, mirroring
  the optional-provider pattern in `agent/llm.py`. Importing this module never
  fails; the deps are only needed when voice is actually used. The install
  command is install-method-aware — see :func:`voice_install_command` (a source
  checkout uses ``uv sync --extra voice``; PyPI users get the matching
  ``uv tool``/``pipx``/``pip`` form). The sounddevice wheels bundle PortAudio on macOS
  and Windows (nothing else to install); on Linux the wheel is pure-Python and
  needs the system library too (e.g. ``sudo apt install libportaudio2``).
- **Cheap availability probe.** :func:`is_voice_available` uses
  ``importlib.util.find_spec`` so a per-render hint check never triggers the
  heavy ``faster_whisper`` / ``ctranslate2`` import; real mic/model failures are
  handled gracefully at record/transcribe time.
- **Model cache.** The Whisper model is loaded once per size and reused — the
  first transcription downloads the model (~75 MB "tiny" … ~460 MB "small");
  subsequent ones are fast.
- **WAV assembled with the stdlib.** Recorded int16 frames are written with the
  standard-library ``wave`` module and decoded back to a float32 array for the
  model, so we never depend on ffmpeg or PyAV's decode path.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import pathlib
import sys
import wave

from yeaboi.config import get_voice_model

logger = logging.getLogger(__name__)

# Whisper models expect 16 kHz mono audio; recording at the target rate avoids a
# resampling step before transcription.
SAMPLE_RATE = 16000
CHANNELS = 1
_SAMPLE_WIDTH_BYTES = 2  # int16

# Loaded WhisperModel instances keyed by size (e.g. "base"). Populated lazily on
# first transcription so the (potentially large) model download happens once.
_MODEL_CACHE: dict = {}


def voice_install_command() -> str:
    """Return the install command that will actually work for *this* install.

    The voice deps live in the ``voice`` extra, but *how* to add an extra depends
    on how yeaboi was installed. A source checkout uses ``uv sync``; PyPI users
    (uv tool / pipx / pip) must reinstall the distribution with the extra. There
    is no single command that fits all, so detect and branch. Detection is
    path-based (not ``importlib.metadata``) because the legacy install may be
    registered under the old distribution name ``scrum-agent``; the package name
    in the returned command is pinned to ``yeaboi`` — the canonical distribution
    that actually publishes the extra.
    """
    # Source checkout (incl. editable / `make install`): pyproject at the repo
    # root two levels above this package. `uv sync --extra voice` is valid there.
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").exists() and (repo_root / "src" / "yeaboi").is_dir():
        return "uv sync --extra voice"

    exe = sys.executable.replace("\\", "/")
    if "/uv/tools/" in exe:
        return "uv tool install 'yeaboi[voice]'"
    if "pipx" in exe or os.environ.get("PIPX_HOME") or os.environ.get("PIPX_BIN_DIR"):
        return "pipx install 'yeaboi[voice]'"
    return "pip install 'yeaboi[voice]'"


def _installed(module_name: str) -> bool:
    """Return True if a module is importable, without importing it.

    Uses find_spec so this stays cheap enough to call on every screen render.
    Treats a sys.modules entry of None (used in tests to simulate absence) and
    any lookup error as "not installed".
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def is_voice_available() -> tuple[bool, str]:
    """Return (available, reason) describing whether voice input can be used.

    Voice needs the optional audio + transcription packages installed. No API
    key is required — transcription is fully local. ``reason`` is empty when
    available, otherwise a short human-readable explanation for the UI.
    """
    if not _installed("sounddevice"):
        # Linux wheels need the system PortAudio lib too.
        return False, f"Install voice extra: {voice_install_command()} (Linux also: apt install libportaudio2)"
    if not _installed("faster_whisper"):
        return False, f"Install voice extra: {voice_install_command()}"
    return True, ""


def is_model_loaded() -> bool:
    """Return True if the model for the configured size is already in memory.

    Lets the UI show a "downloading model" message on the first transcription
    instead of a bare "transcribing" that could hang for a while.
    """
    return get_voice_model() in _MODEL_CACHE


def backend_label() -> str:
    """Short human-readable description of the transcription backend (for Settings)."""
    return f"local Whisper ({get_voice_model()})"


class Recorder:
    """Records microphone audio into memory until :meth:`stop` is called.

    Uses a ``sounddevice.InputStream`` with a callback that appends each audio
    block to a list — this lets the caller stop recording on an arbitrary event
    (e.g. a keypress) rather than committing to a fixed duration up front.
    """

    def __init__(self, samplerate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        import numpy as np  # noqa: F401 - imported to fail fast if numpy is absent
        import sounddevice as sd

        self.samplerate = samplerate
        self.channels = channels
        self._frames: list = []
        self._stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Voice recording started: %d Hz, %d ch", samplerate, channels)

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:  # pragma: no cover - hardware-dependent (overflows etc.)
            logger.debug("Audio input status: %s", status)
        # Copy — sounddevice reuses the underlying buffer across callbacks.
        self._frames.append(indata.copy())

    def stop(self) -> bytes:
        """Stop the stream and return the recording as WAV-encoded bytes.

        Returns empty bytes if nothing was captured (e.g. immediate stop).
        """
        import numpy as np

        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # pragma: no cover - defensive; stream already closed
            logger.warning("Error closing audio stream", exc_info=True)

        if not self._frames:
            logger.info("Voice recording stopped: no audio captured")
            return b""

        data = np.concatenate(self._frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(_SAMPLE_WIDTH_BYTES)
            wav.setframerate(self.samplerate)
            wav.writeframes(data.tobytes())
        wav_bytes = buf.getvalue()
        logger.info("Voice recording stopped: %d bytes WAV", len(wav_bytes))
        return wav_bytes


def _get_model():
    """Return a cached faster-whisper model for the configured size.

    The first call for a given size loads (and, if missing, downloads) the
    model. device="cpu"/compute_type="int8" is the broadly-compatible default.
    """
    size = get_voice_model()
    model = _MODEL_CACHE.get(size)
    if model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading local Whisper model: size=%s (first run may download it)", size)
        model = WhisperModel(size, device="cpu", compute_type="int8")
        _MODEL_CACHE[size] = model
    return model


def transcribe(wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text locally via faster-whisper.

    Returns the transcript (stripped), or an empty string if there is no audio.
    Raises on model-load/transcription errors so the caller can surface them.
    """
    if not wav_bytes:
        return ""

    import numpy as np

    # Decode the WAV int16 PCM back to the float32 mono array the model expects,
    # avoiding any ffmpeg/PyAV decode dependency.
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    model = _get_model()
    logger.info("Transcribing %d samples with local Whisper", len(samples))
    segments, _info = model.transcribe(samples, beam_size=5)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info("Transcription complete: %d chars", len(text))
    return text
