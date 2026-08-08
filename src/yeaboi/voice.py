"""Voice input — record from the microphone and transcribe locally (offline).

This module lets users *speak* their answers instead of typing them. It is used
by the TUI text-entry loops (planning chat, project description, intake question
answers, standup fields and the artifact editor), which start recording on a
double-tap of the space bar — see :class:`~yeaboi.ui.shared._voice_input.DoubleTapSpace`
for why that gesture and not a modifier chord.

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
- **The microphone is chosen, not assumed.** PortAudio\'s default input is
  frequently not the one the user means, and it caches its device list at
  init — so a mic plugged in mid-session is invisible until the library is
  cycled (:func:`refresh_devices`). Device selection lives in
  :func:`resolve_device` / ``VOICE_DEVICE``, and :class:`Recorder` falls back to
  the device\'s own sample rate and channel count when it refuses 16 kHz mono,
  with :func:`transcribe` converting. Before that, such a mic simply failed.
- **Model cache.** The Whisper model is loaded once per size and reused — the
  first transcription downloads the model (~75 MB "tiny" … ~460 MB "small");
  subsequent ones are fast.
- **WAV assembled with the stdlib.** Recorded int16 frames are written with the
  standard-library ``wave`` module and decoded back to a float32 array for the
  model, so the *host recording* path never depends on ffmpeg or PyAV's decode
  path. The one deliberate exception is :func:`transcribe_media`, which accepts
  browser ``MediaRecorder`` blobs (webm/opus, or mp4 on Safari) — those arrive
  in container formats the stdlib cannot decode, and faster-whisper already
  hard-depends on PyAV, so handing the bytes straight to the model adds no new
  dependency.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import pathlib
import sys
import threading
import wave

from yeaboi.config import get_voice_device, get_voice_model

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


def _module_check() -> tuple[bool, str]:
    """Are both optional packages on the import path? Two find_spec calls, no more."""
    if not _installed("sounddevice"):
        # Linux wheels need the system PortAudio lib too.
        return False, f"Install voice extra: {voice_install_command()} (Linux also: apt install libportaudio2)"
    if not _installed("faster_whisper"):
        return False, f"Install voice extra: {voice_install_command()}"
    return True, ""


def is_voice_available() -> tuple[bool, str]:
    """Return (available, reason) describing whether voice input can be used.

    Voice needs the optional audio + transcription packages installed. No API
    key is required — transcription is fully local. ``reason`` is empty when
    available, otherwise a short human-readable explanation for the UI.

    Cheap by contract: this is called on every input-box render, so it only asks
    the import system. It will *report* a strict failure that
    :func:`probe_voice_backend` has already found (a present-but-unusable
    PortAudio), but it never goes looking for one.
    """
    available, reason = _module_check()
    if available and _backend_probe is not None and not _backend_probe[0]:
        return _backend_probe
    return available, reason


# Result of the last strict backend probe, cached for the life of the process.
# See probe_voice_backend for why this is separate from the cheap find_spec path.
_backend_probe: tuple[bool, str] | None = None


def reset_probe() -> None:
    """Forget the cached strict probe result.

    Called by :func:`yeaboi.voice_install.refresh_imports` after an in-app
    install, and by tests that fake availability.
    """
    global _backend_probe
    _backend_probe = None


def _silence_stderr():  # noqa: ANN202 - contextmanager factory, typed by the decorator
    """Redirect file descriptor 2 to /dev/null for the duration of the block.

    PortAudio writes ALSA and JACK warnings straight to fd 2 from C, which
    ``contextlib.redirect_stderr`` cannot catch (it only rebinds ``sys.stderr``).
    Under a Rich ``Live`` those bytes land in the middle of a frame and corrupt
    the screen, so initialising PortAudio has to happen with the descriptor
    pointed elsewhere.
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():  # noqa: ANN202
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            saved = os.dup(2)
        except OSError:  # pragma: no cover - no fd 2 (rare, e.g. some daemons)
            yield
            return
        try:
            os.dup2(devnull, 2)
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(devnull)

    return _ctx()


def probe_voice_backend(*, force: bool = False) -> tuple[bool, str]:
    """Strict availability check — actually opens PortAudio. Never per frame.

    :func:`is_voice_available` only asks whether the modules are on the path,
    which on Linux is not the same question: the ``sounddevice`` wheel is pure
    Python and imports happily without the system PortAudio library, then fails
    at the first recording. That made the app promise a feature it could not
    deliver, with an error arriving only once the user had already tried to
    speak.

    This runs once per process, at the moment dictation is requested, where a
    ~100 ms hitch is invisible and an honest error is worth having. The result
    is cached and mirrored back into :func:`is_voice_available` so the render
    path stays exactly as cheap as it was.
    """
    global _backend_probe
    if _backend_probe is not None and not force:
        return _backend_probe

    available, reason = _module_check()
    if not available:
        _backend_probe = (available, reason)
        return _backend_probe

    try:
        with _silence_stderr():
            import sounddevice as sd

            sd.query_devices()
    except Exception as exc:  # noqa: BLE001 - a missing native lib surfaces as OSError here or at import
        logger.warning("PortAudio unavailable: %s", exc)
        hint = (
            " — sudo apt install libportaudio2 (or your distro's equivalent)"
            if sys.platform.startswith("linux")
            else ""
        )
        _backend_probe = (False, f"Audio backend unavailable{hint}")
        return _backend_probe

    _backend_probe = (True, "")
    return _backend_probe


def voice_state() -> str:
    """One vocabulary for every dictation surface: how to talk about voice here.

    Returns ``"ready"``, ``"installable"``, ``"declined"`` or ``"unsupported"``.
    The chip, the input hint, the welcome tip and the Settings row all render
    from this rather than each deciding for itself, which is how they used to
    end up saying different things about the same machine.
    """
    if is_voice_available()[0]:
        return "ready"
    if unsupported_blocker():
        return "unsupported"

    from yeaboi.config import is_voice_install_offer_enabled

    return "installable" if is_voice_install_offer_enabled() else "declined"


def unsupported_blocker() -> str:
    """Why dictation cannot work here at all, or ``""`` if installing would help.

    There are two ways to be beyond help, and every surface needs the same
    sentence for both:

    1. No wheel can exist for this platform, or a past install failed
       permanently — :func:`yeaboi.voice_install.unsupported_reason`.
    2. The packages are *already* installed and something else is broken. The
       common one is a Linux host with no ``libportaudio2``: ``sounddevice`` is
       a pure-Python wheel that imports happily without it, so the modules are
       present and an install would exit 0 having changed nothing. Offering one
       would talk the user through ~325 MB and two minutes to arrive back
       exactly where they started — on the very platform the strict probe was
       added for.

    Cheap enough for the render path: a find_spec pair plus a memoised read. It
    never *starts* a strict probe, it only reports one that already ran.
    """
    from yeaboi import voice_install

    verdict = voice_install.unsupported_reason()
    if verdict:
        return verdict
    if _module_check()[0]:
        available, reason = is_voice_available()
        if not available:
            return reason
    return ""


def is_model_loaded() -> bool:
    """Return True if the model for the configured size is already in memory.

    Lets the UI show a "downloading model" message on the first transcription
    instead of a bare "transcribing" that could hang for a while.
    """
    return get_voice_model() in _MODEL_CACHE


def backend_label() -> str:
    """Short human-readable description of the transcription backend (for Settings)."""
    return f"local Whisper ({get_voice_model()})"


# ---------------------------------------------------------------------------
# Input devices
# ---------------------------------------------------------------------------

# Number of InputStreams currently open, so refresh_devices() can refuse to
# cycle PortAudio underneath a live recording (see refresh_devices).
_open_streams = 0
_stream_lock = threading.Lock()


def refresh_devices() -> bool:
    """Re-scan the host's audio devices. Returns True if a rescan actually ran.

    PortAudio enumerates devices **once**, when it initialises, and caches that
    list for the life of the process. So a USB or Bluetooth microphone plugged
    in after yeaboi started is invisible to ``query_devices`` — which is the
    single most common cause of "it doesn't detect my external mic". The only
    way to see it is to cycle the library, which sounddevice exposes as the
    private ``_terminate``/``_initialize`` pair; being private API, every call
    here is defensive.

    The teardown is process-wide, so it must not run while any stream is open —
    the poker duel holds a Recorder on a server thread for a whole duel, and
    cycling PortAudio under it would kill that recording. The count is therefore
    checked *and* the cycle performed under one hold of ``_stream_lock``: with a
    check-then-act, a Recorder opened in the gap would have PortAudio torn down
    underneath it, which is exactly the case the guard exists to prevent.
    ``Recorder.__init__`` takes the same lock, so it blocks here rather than
    racing.
    """
    with _stream_lock:
        if _open_streams:
            logger.info("Skipping audio device rescan: %d stream(s) open", _open_streams)
            return False
        try:
            import sounddevice as sd

            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001 - private API; a failed rescan is not fatal
            logger.warning("Audio device rescan failed", exc_info=True)
            return False
    logger.info("Audio devices rescanned")
    return True


def list_input_devices() -> list[dict]:
    """Return the host's usable input devices.

    Each entry is ``{"index", "name", "channels", "samplerate", "is_default"}``.
    Devices with no input channels (speakers, virtual outputs) are filtered out.
    Returns an empty list if sounddevice is missing or the query fails, so
    callers can render "no microphones found" rather than crash.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        try:
            default_index = sd.default.device[0]
        except Exception:  # noqa: BLE001 - no default configured on this host
            default_index = None
    except Exception:  # noqa: BLE001 - sounddevice absent or PortAudio unhappy
        logger.warning("Could not list audio input devices", exc_info=True)
        return []

    out: list[dict] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        out.append(
            {
                "index": index,
                "name": str(device.get("name", f"device {index}")),
                "channels": int(device.get("max_input_channels", 1)),
                "samplerate": int(device.get("default_samplerate", SAMPLE_RATE) or SAMPLE_RATE),
                "is_default": index == default_index,
            }
        )
    return out


def resolve_device(pref: str | None = None) -> int | None:
    """Resolve a VOICE_DEVICE preference to a PortAudio device index.

    Accepts an index (``"2"``) or a case-insensitive name substring
    (``"shure"``) so users can type something memorable instead of an index
    that renumbers whenever a device is unplugged. Returns ``None`` for "use
    PortAudio's default", which is exactly the behaviour before this setting
    existed — an unset or unmatched preference must never block recording.
    """
    pref = (pref if pref is not None else get_voice_device()).strip()
    if not pref:
        return None

    devices = list_input_devices()
    if pref.lstrip("-").isdigit():
        index = int(pref)
        if any(d["index"] == index for d in devices):
            return index
        logger.warning("Configured voice device index %s not found; using the system default", index)
        return None

    needle = pref.casefold()
    for device in devices:
        if needle in device["name"].casefold():
            return device["index"]
    logger.warning("Configured voice device %r not found; using the system default", pref)
    return None


def device_name(index: int | None) -> str:
    """Human-readable name for a device index (the system default when None)."""
    for device in list_input_devices():
        if device["index"] == index or (index is None and device["is_default"]):
            return device["name"]
    return "system default"


class Recorder:
    """Records microphone audio into memory until :meth:`stop` is called.

    Uses a ``sounddevice.InputStream`` with a callback that appends each audio
    block to a list — this lets the caller stop recording on an arbitrary event
    (e.g. a keypress) rather than committing to a fixed duration up front.

    Two things it does beyond opening a stream:

    - **Device selection.** ``device`` is a PortAudio index (``None`` = the
      system default). Callers resolve a user preference with
      :func:`resolve_device`.
    - **Format negotiation.** 16 kHz mono is what Whisper wants, but plenty of
      USB and Bluetooth microphones simply refuse it — PortAudio then raises out
      of the constructor and, before this, the whole feature looked broken on
      that hardware. So a rejection is retried at the device\'s own default
      format and the resulting WAV carries its real rate/channel count;
      :func:`transcribe` converts. Recording native and converting once beats
      not recording at all.
    - **Monitor mode.** ``monitor=True`` keeps :meth:`level` live but discards
      every block instead of retaining it. The Settings mic test runs for as
      long as the user leaves the page open, and a retained take at 48 kHz
      stereo grows by ~11 MB a minute for a WAV nobody ever asks for.
    """

    def __init__(
        self,
        samplerate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        device: int | None = None,
        monitor: bool = False,
    ) -> None:
        import numpy as np  # noqa: F401 - imported to fail fast if numpy is absent
        import sounddevice as sd

        self.device = device
        self.device_name = device_name(device)
        self.samplerate = samplerate
        self.channels = channels
        self.monitor = monitor
        self._frames: list = []
        self._level = 0.0
        self._counted = False

        # The whole open-and-count runs under _stream_lock so refresh_devices()
        # cannot cycle PortAudio between the stream being created and the
        # counter that tells it not to. Counting *before* start() rather than
        # after closes the same gap from the other side: a stream that exists
        # but is not yet counted is still a stream a teardown would break.
        with _stream_lock:
            try:
                self._stream = self._open(sd, samplerate, channels)
            except Exception as rejected:  # noqa: BLE001 - re-raised below if unrecoverable
                # The net is deliberately wide: PortAudio surfaces an unsupported
                # format as PortAudioError on some hosts and ValueError on others.
                fallback = self._device_format(sd)
                if fallback is None or fallback == (samplerate, channels):
                    raise
                logger.info(
                    "Mic %r rejected %d Hz/%d ch (%s); retrying at %d Hz/%d ch",
                    self.device_name,
                    samplerate,
                    channels,
                    rejected,
                    *fallback,
                )
                self.samplerate, self.channels = fallback
                self._stream = self._open(sd, *fallback)

            global _open_streams
            _open_streams += 1
            self._counted = True
            try:
                self._stream.start()
            except Exception:
                # Undo the count, or a stream that never ran would block every
                # future rescan for the life of the process, and close the
                # stream itself — the constructor is about to raise, so nobody
                # is left holding a reference to close it later.
                _open_streams -= 1
                self._counted = False
                try:
                    self._stream.close()
                except Exception:  # noqa: BLE001 - already failing; the original error is what matters
                    logger.warning("Could not close a stream that failed to start", exc_info=True)
                raise
        logger.info(
            "Voice recording started: %s, %d Hz, %d ch%s",
            self.device_name,
            self.samplerate,
            self.channels,
            " (monitor only)" if monitor else "",
        )

    def _open(self, sd, samplerate: int, channels: int):
        return sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            device=self.device,
            callback=self._callback,
        )

    def _device_format(self, sd) -> tuple[int, int] | None:
        """The device\'s own preferred (samplerate, channels), or None."""
        try:
            info = sd.query_devices(self.device) if self.device is not None else sd.query_devices(kind="input")
            rate = int(info.get("default_samplerate") or 0)
            channels = min(2, int(info.get("max_input_channels", 0)))
        except Exception:  # noqa: BLE001 - no device info to fall back on
            logger.warning("Could not read the microphone's default format", exc_info=True)
            return None
        if rate <= 0 or channels <= 0:
            return None
        return rate, channels

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:  # pragma: no cover - hardware-dependent (overflows etc.)
            logger.debug("Audio input status: %s", status)
        # Peak of this block, normalised to 0..1, for the recording level meter.
        # Computed here rather than at render time because this is the only place
        # the raw audio exists — and never logged, since this runs per block.
        self._level = float(abs(indata).max()) / 32768.0
        if self.monitor:
            return  # level only — see the class docstring on monitor mode
        # Copy — sounddevice reuses the underlying buffer across callbacks.
        self._frames.append(indata.copy())

    def level(self) -> float:
        """Peak amplitude (0..1) of the most recent audio block.

        Drives the meter that tells the user their microphone is actually being
        heard — a flat meter is the difference between "yeaboi is broken" and
        "that mic is not the one picking you up".
        """
        return self._level

    def stop(self) -> bytes:
        """Stop the stream and return the recording as WAV-encoded bytes.

        The WAV carries the *negotiated* rate and channel count, not necessarily
        16 kHz mono — see the class docstring.

        Returns empty bytes if nothing was captured (e.g. immediate stop).
        """
        import numpy as np

        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # pragma: no cover - defensive; stream already closed
            logger.warning("Error closing audio stream", exc_info=True)
        finally:
            with _stream_lock:
                global _open_streams
                if self._counted:
                    _open_streams -= 1
                    self._counted = False

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


def _downmix(samples, channels: int):
    """Average a interleaved multi-channel float array down to mono.

    Whisper wants one channel. Many USB interfaces will only open as stereo (see
    :class:`Recorder`\'s format negotiation), so the recording that reaches us is
    not necessarily mono even though we asked for mono.
    """
    import numpy as np

    usable = (len(samples) // channels) * channels  # drop a torn trailing frame
    return np.asarray(samples[:usable], dtype=np.float32).reshape(-1, channels).mean(axis=1)


def _resample(samples, src_rate: int, dst_rate: int):
    """Resample a mono float array from ``src_rate`` to ``dst_rate``.

    Linear interpolation via ``np.interp`` — speech at 16 kHz does not justify a
    polyphase filter, and this keeps the dependency set at numpy (already a
    faster-whisper transitive dep) rather than adding scipy or an ffmpeg shell-out.
    Downsampling is preceded by a moving-average low pass, because decimating
    48 kHz straight to 16 kHz folds everything above 8 kHz back into the speech
    band as aliasing noise, which measurably degrades the transcript.
    """
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    n_out = int(len(samples) * dst_rate / src_rate)
    # Guard before filtering: np.convolve(mode="same") returns max(len(signal),
    # len(kernel)) samples, so a kernel wider than a near-empty take would grow it.
    if n_out <= 0 or len(samples) < 2:
        return np.zeros(0, dtype=np.float32)

    if src_rate > dst_rate:
        width = int(round(src_rate / dst_rate))
        if 1 < width < len(samples):
            kernel = np.ones(width, dtype=np.float32) / width
            samples = np.convolve(samples, kernel, mode="same")
    positions = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def transcribe(wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text locally via faster-whisper.

    Accepts any sample rate / channel count: the recorder falls back to whatever
    format the microphone will actually open with (many USB and Bluetooth mics
    refuse 16 kHz mono), so conversion to the 16 kHz mono the model expects
    happens here rather than being assumed at capture time.

    Returns the transcript (stripped), or an empty string if there is no audio.
    Raises on model-load/transcription errors so the caller can surface them.
    """
    if not wav_bytes:
        return ""

    import numpy as np

    # Decode the WAV int16 PCM back to the float32 array the model expects,
    # avoiding any ffmpeg/PyAV decode dependency.
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        frames = wav.readframes(wav.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    # Both conversions are skipped for already-conforming audio, so the common
    # path stays exactly as cheap as it was before.
    if channels > 1:
        samples = _downmix(samples, channels)
    if rate != SAMPLE_RATE:
        logger.info("Resampling recording: %d Hz, %d ch -> %d Hz mono", rate, channels, SAMPLE_RATE)
        samples = _resample(samples, rate, SAMPLE_RATE)

    model = _get_model()
    logger.info("Transcribing %d samples with local Whisper", len(samples))
    segments, _info = model.transcribe(samples, beam_size=5)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info("Transcription complete: %d chars", len(text))
    return text


def transcribe_media(data: bytes) -> str:
    """Transcribe browser-recorded audio (webm/opus, mp4) via faster-whisper.

    Used by the poker duel feature: duelists' browsers upload ``MediaRecorder``
    blobs whose container format the stdlib ``wave`` module cannot read. This is
    the documented exception to the module's "no ffmpeg/PyAV decode" note — that
    note is about the HOST recording path, which stays stdlib-WAV. faster-whisper
    hard-depends on PyAV anyway, so passing a file-like object straight to
    ``model.transcribe()`` decodes these containers with zero new dependencies.

    Returns the transcript (stripped), or an empty string for empty input.
    Raises on decode/model errors so the caller can handle each blob separately.
    """
    if not data:
        return ""

    model = _get_model()
    logger.info("Transcribing %d-byte media blob with local Whisper", len(data))
    segments, _info = model.transcribe(io.BytesIO(data), beam_size=5)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logger.info("Media transcription complete: %d chars", len(text))
    return text
