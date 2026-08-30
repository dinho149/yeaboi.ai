"""Unit tests for voice input — mic recording, local Whisper transcription, overlay.

The audio/transcription packages (sounddevice, numpy, faster-whisper) are
optional and not installed in the test environment, so these tests inject fake
modules into sys.modules to exercise the lazy-import code paths. Transcription
runs locally (no API key), so there is nothing OpenAI-specific to mock.
"""

from __future__ import annotations

import importlib.machinery
import io
import logging
import sys
import time
import types
import wave

import pytest
from rich.console import Console
from rich.text import Text

from yeaboi import voice
from yeaboi.config import get_voice_device, get_voice_model

# ---------------------------------------------------------------------------
# Fakes for the optional dependencies
# ---------------------------------------------------------------------------


class _FakeNdarray:
    """Minimal stand-in for a numpy array covering the ops voice.py uses."""

    def __init__(self, data: bytes = b"", n: int = 0, peak: int = 0) -> None:
        self._data = data
        self._n = n
        self._peak = peak

    def copy(self) -> _FakeNdarray:
        return self

    def astype(self, _dtype) -> _FakeNdarray:
        return self

    def __truediv__(self, _other) -> _FakeNdarray:
        return self

    def __len__(self) -> int:
        return self._n

    def tobytes(self) -> bytes:
        return self._data

    # Recorder._callback computes abs(block).max() for the level meter.
    def __abs__(self) -> _FakeNdarray:
        return self

    def max(self):
        return self._peak


def _fake_numpy() -> types.ModuleType:
    mod = types.ModuleType("numpy")
    mod.int16 = "int16"
    mod.float32 = "float32"
    mod.concatenate = lambda frames, axis=0: _FakeNdarray(b"".join(f.tobytes() for f in frames))
    mod.frombuffer = lambda buf, dtype=None: _FakeNdarray(bytes(buf), n=len(bytes(buf)) // 2)
    return mod


class _FakeInputStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


# A plausible PortAudio device table: the built-in mic, a 2-channel USB
# interface, and an output-only device that must be filtered out of the input
# list. Real query_devices() dicts carry many more keys; these are the ones
# voice.py reads.
_FAKE_DEVICES = [
    {
        "name": "MacBook Pro Microphone",
        "max_input_channels": 1,
        "max_output_channels": 0,
        "default_samplerate": 48000.0,
    },
    {"name": "Shure MV7 (USB)", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 44100.0},
    {
        "name": "Studio Display Speakers",
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    },
]


def _fake_sounddevice(*, devices=None, default_input: int = 0, reject=None) -> types.ModuleType:
    """Fake sounddevice module.

    ``reject`` is an optional ``(kwargs) -> bool`` predicate; when it returns
    True the InputStream constructor raises PortAudioError. That stands in for
    the real failure this feature exists to survive — a USB mic that refuses
    16 kHz mono and only speaks its own default rate.
    """
    mod = types.ModuleType("sounddevice")
    devs = list(_FAKE_DEVICES if devices is None else devices)

    class PortAudioError(Exception):
        pass

    class _Stream(_FakeInputStream):
        def __init__(self, **kwargs) -> None:
            if reject is not None and reject(kwargs):
                raise PortAudioError("Invalid sample rate [PaErrorCode -9997]")
            super().__init__(**kwargs)

    def query_devices(index=None, kind=None):
        # Real sounddevice returns a DeviceList for the no-arg call, a single
        # dict when given an index, and the *default* device for that kind when
        # given only kind= — voice.py uses all three shapes.
        if index is not None:
            return devs[index]
        if kind == "input":
            return devs[default_input]
        return list(devs)

    mod.InputStream = _Stream
    mod.PortAudioError = PortAudioError
    mod.query_devices = query_devices
    mod.default = types.SimpleNamespace(device=(default_input, 1))
    mod.calls = []  # records _terminate/_initialize for the refresh test
    mod._terminate = lambda: mod.calls.append("terminate")
    mod._initialize = lambda: mod.calls.append("initialize")
    return mod


def _fake_faster_whisper(captured: dict, text_segments=("  hello ", "world  ")) -> types.ModuleType:
    mod = types.ModuleType("faster_whisper")

    class _Segment:
        def __init__(self, text: str) -> None:
            self.text = text

    class WhisperModel:
        def __init__(self, size, device=None, compute_type=None) -> None:
            captured["size"] = size
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, samples, beam_size=None):
            captured["beam_size"] = beam_size
            captured["n_samples"] = len(samples)
            return ([_Segment(t) for t in text_segments], object())

    mod.WhisperModel = WhisperModel
    return mod


def _with_spec(mod: types.ModuleType) -> types.ModuleType:
    """Attach a ModuleSpec so importlib.util.find_spec treats it as installed."""
    mod.__spec__ = importlib.machinery.ModuleSpec(mod.__name__, loader=None)
    return mod


@pytest.fixture(autouse=True)
def _clear_model_cache():
    voice._MODEL_CACHE.clear()
    yield
    voice._MODEL_CACHE.clear()


@pytest.fixture(autouse=True)
def _isolate_stream_count(monkeypatch):
    """Start every test with the open-stream counter at zero.

    ``voice._open_streams`` is process-global: it exists so refresh_devices()
    can refuse to tear PortAudio down under a live recording. That makes it the
    one piece of state in this module that outlives a test — anything that
    constructs a Recorder and does not stop it (a poker duel left open, say)
    leaves the count at 1, and every later ``refresh_devices()`` in the session
    returns False. Several tests below assert on exactly that return value, so
    pinning the baseline keeps them measuring the code rather than whatever ran
    before them.
    """
    monkeypatch.setattr(voice, "_open_streams", 0)


@pytest.fixture
def _inject(monkeypatch):
    """Inject fake optional modules; returns a helper to toggle presence."""

    def install(
        *,
        numpy=True,
        sounddevice=True,
        faster_whisper_captured=None,
        segments=("  hello ", "world  "),
        devices=None,
        default_input=0,
        reject=None,
    ):
        """Install the fakes; returns the fake sounddevice module (or None).

        Pass ``numpy=False`` to let the *real* numpy through — the hand-rolled
        fake cannot stand in for np.interp, so the resampling tests need it.
        """
        if numpy:
            monkeypatch.setitem(sys.modules, "numpy", _fake_numpy())
        sd = None
        if sounddevice:
            sd = _fake_sounddevice(devices=devices, default_input=default_input, reject=reject)
            monkeypatch.setitem(sys.modules, "sounddevice", _with_spec(sd))
        else:
            monkeypatch.setitem(sys.modules, "sounddevice", None)
        if faster_whisper_captured is not None:
            monkeypatch.setitem(
                sys.modules, "faster_whisper", _with_spec(_fake_faster_whisper(faster_whisper_captured, segments))
            )
        return sd

    return install


def _wav_bytes(pcm: bytes = b"\x01\x00\x02\x00", *, rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _tone_wav(rate: int, channels: int, seconds: float = 0.05) -> bytes:
    """A real int16 sine take, for the resampling tests (needs real numpy)."""
    import math
    import struct

    n = int(rate * seconds)
    samples = []
    for i in range(n):
        value = int(20000 * math.sin(2 * math.pi * 440 * i / rate))
        samples.extend([value] * channels)
    return _wav_bytes(struct.pack(f"<{len(samples)}h", *samples), rate=rate, channels=channels)


# ---------------------------------------------------------------------------
# get_voice_model / backend_label
# ---------------------------------------------------------------------------


class TestVoiceModel:
    def test_default_is_base(self, monkeypatch):
        monkeypatch.delenv("VOICE_MODEL", raising=False)
        assert get_voice_model() == "base"

    def test_override(self, monkeypatch):
        monkeypatch.setenv("VOICE_MODEL", "small")
        assert get_voice_model() == "small"

    def test_backend_label(self, monkeypatch):
        monkeypatch.setenv("VOICE_MODEL", "tiny")
        assert voice.backend_label() == "local Whisper (tiny)"


class TestVoiceDeviceSetting:
    """get_voice_device stays a plain string read — resolution to a PortAudio
    index happens in voice.resolve_device, so config never imports the audio
    stack."""

    def test_unset_means_the_system_default(self, monkeypatch):
        monkeypatch.delenv("VOICE_DEVICE", raising=False)
        assert get_voice_device() == ""

    def test_reads_a_name_substring(self, monkeypatch):
        monkeypatch.setenv("VOICE_DEVICE", "shure")
        assert get_voice_device() == "shure"

    def test_reads_an_index(self, monkeypatch):
        monkeypatch.setenv("VOICE_DEVICE", "2")
        assert get_voice_device() == "2"

    def test_surrounding_whitespace_is_stripped(self, monkeypatch):
        """A value hand-edited into ~/.yeaboi/.env often carries a stray space."""
        monkeypatch.setenv("VOICE_DEVICE", "  Shure MV7  ")
        assert get_voice_device() == "Shure MV7"

    def test_whitespace_only_is_the_system_default(self, monkeypatch):
        monkeypatch.setenv("VOICE_DEVICE", "   ")
        assert get_voice_device() == ""


# ---------------------------------------------------------------------------
# is_voice_available — no API key required (fully local)
# ---------------------------------------------------------------------------


class TestVoiceInstallCommand:
    """The install hint must match how yeaboi was actually installed."""

    def _no_source_checkout(self, monkeypatch):
        # Force the source-checkout branch to miss so path-based detection runs.
        # (When the tests run from a checkout, pyproject.toml exists at the repo
        # root and would otherwise short-circuit to `uv sync`.)
        monkeypatch.setattr(voice.pathlib.Path, "exists", lambda self: False)

    def test_source_checkout(self, monkeypatch):
        # pyproject.toml + src/yeaboi both present -> source checkout.
        monkeypatch.setattr(voice.pathlib.Path, "exists", lambda self: True)
        monkeypatch.setattr(voice.pathlib.Path, "is_dir", lambda self: True)
        assert voice.voice_install_command() == "uv sync --extra voice"

    def test_uv_tool_install(self, monkeypatch):
        self._no_source_checkout(monkeypatch)
        monkeypatch.setattr(voice.sys, "executable", "/home/u/.local/share/uv/tools/yeaboi/bin/python")
        assert voice.voice_install_command() == "uv tool install 'yeaboi[voice]'"

    def test_pipx_by_path(self, monkeypatch):
        self._no_source_checkout(monkeypatch)
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("PIPX_BIN_DIR", raising=False)
        monkeypatch.setattr(voice.sys, "executable", "/home/u/.local/pipx/venvs/yeaboi/bin/python")
        assert voice.voice_install_command() == "pipx install 'yeaboi[voice]'"

    def test_pipx_by_env(self, monkeypatch):
        self._no_source_checkout(monkeypatch)
        monkeypatch.setattr(voice.sys, "executable", "/usr/bin/python3")
        monkeypatch.setenv("PIPX_HOME", "/home/u/.local/pipx")
        assert voice.voice_install_command() == "pipx install 'yeaboi[voice]'"

    def test_pip_fallback(self, monkeypatch):
        self._no_source_checkout(monkeypatch)
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("PIPX_BIN_DIR", raising=False)
        monkeypatch.setattr(voice.sys, "executable", "/usr/bin/python3")
        assert voice.voice_install_command() == "pip install 'yeaboi[voice]'"


class TestIsVoiceAvailable:
    def test_missing_sounddevice(self, _inject):
        _inject(sounddevice=False, faster_whisper_captured={})
        available, reason = voice.is_voice_available()
        assert available is False
        assert "voice" in reason.lower()

    def test_missing_faster_whisper(self, monkeypatch, _inject):
        _inject(faster_whisper_captured=None)  # sounddevice present, faster_whisper absent
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        available, reason = voice.is_voice_available()
        assert available is False

    def test_available_without_api_key(self, monkeypatch, _inject):
        # Explicitly ensure no OpenAI key is needed anymore.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _inject(faster_whisper_captured={})
        available, reason = voice.is_voice_available()
        assert available is True
        assert reason == ""


# ---------------------------------------------------------------------------
# Input devices
# ---------------------------------------------------------------------------


class TestListInputDevices:
    def test_filters_out_output_only_devices(self, _inject):
        _inject()
        names = [d["name"] for d in voice.list_input_devices()]
        assert names == ["MacBook Pro Microphone", "Shure MV7 (USB)"]

    def test_marks_the_system_default(self, _inject):
        _inject(default_input=1)
        defaults = [d["name"] for d in voice.list_input_devices() if d["is_default"]]
        assert defaults == ["Shure MV7 (USB)"]

    def test_reports_channels_and_rate(self, _inject):
        _inject()
        usb = voice.list_input_devices()[1]
        assert usb["index"] == 1
        assert usb["channels"] == 2
        assert usb["samplerate"] == 44100

    def test_missing_sounddevice_returns_empty(self, _inject):
        _inject(sounddevice=False)
        assert voice.list_input_devices() == []

    def test_missing_sounddevice_logs_one_info_line_not_a_warning(self, _inject, caplog, monkeypatch):
        _inject(sounddevice=False)
        monkeypatch.setattr(voice, "_sounddevice_missing_logged", False)
        with caplog.at_level(logging.INFO, logger="yeaboi.voice"):
            assert voice.list_input_devices() == []
            assert voice.list_input_devices() == []
        notes = [r for r in caplog.records if "sounddevice not installed" in r.message]
        assert len(notes) == 1
        assert all(r.levelno < logging.WARNING for r in caplog.records)


class TestResolveDevice:
    def test_blank_preference_means_system_default(self, _inject):
        _inject()
        assert voice.resolve_device("") is None

    def test_resolves_by_name_substring_case_insensitively(self, _inject):
        _inject()
        assert voice.resolve_device("shure") == 1

    def test_resolves_by_index(self, _inject):
        _inject()
        assert voice.resolve_device("1") == 1

    def test_unknown_name_falls_back_to_default(self, _inject):
        _inject()
        assert voice.resolve_device("Blue Yeti") is None

    def test_out_of_range_index_falls_back_to_default(self, _inject):
        _inject()
        assert voice.resolve_device("9") is None

    def test_output_only_device_is_not_selectable(self, _inject):
        _inject()
        assert voice.resolve_device("Studio Display") is None

    def test_reads_the_env_var_when_no_preference_passed(self, monkeypatch, _inject):
        _inject()
        monkeypatch.setenv("VOICE_DEVICE", "MV7")
        assert voice.resolve_device() == 1


class TestDeviceName:
    def test_names_an_index(self, _inject):
        _inject()
        assert voice.device_name(1) == "Shure MV7 (USB)"

    def test_none_names_the_default(self, _inject):
        _inject(default_input=1)
        assert voice.device_name(None) == "Shure MV7 (USB)"

    def test_unknown_index_is_described_generically(self, _inject):
        _inject(sounddevice=False)
        assert voice.device_name(3) == "system default"


class TestListAudioDevicesCommand:
    """`yeaboi --list-audio-devices` — the "why can\'t it hear my mic" diagnostic."""

    def test_reports_when_voice_is_not_installed(self, monkeypatch, capsys):
        from yeaboi import cli

        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "Install voice extra: …"))
        cli._list_audio_devices()
        assert "unavailable" in capsys.readouterr().out

    def test_lists_devices_and_marks_the_selection(self, monkeypatch, capsys, _inject):
        from yeaboi import cli

        _inject()
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        monkeypatch.setenv("VOICE_DEVICE", "MV7")
        cli._list_audio_devices()
        out = capsys.readouterr().out
        assert "MacBook Pro Microphone" in out
        assert "system default" in out
        assert "selected via VOICE_DEVICE=MV7" in out
        assert "Studio Display Speakers" not in out  # output-only

    def test_rescans_before_listing(self, monkeypatch, _inject):
        """A mic plugged in after launch is invisible without the rescan."""
        from yeaboi import cli

        sd = _inject()
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        cli._list_audio_devices()
        assert sd.calls == ["terminate", "initialize"]

    def test_says_so_when_there_are_no_microphones(self, monkeypatch, capsys, _inject):
        from yeaboi import cli

        _inject(devices=[])
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        cli._list_audio_devices()
        assert "No microphones found" in capsys.readouterr().out


class TestRefreshDevices:
    def test_cycles_portaudio(self, _inject):
        sd = _inject()
        assert voice.refresh_devices() is True
        assert sd.calls == ["terminate", "initialize"]

    def test_refuses_while_a_stream_is_open(self, _inject):
        sd = _inject()
        rec = voice.Recorder()
        try:
            assert voice.refresh_devices() is False
            assert sd.calls == []  # PortAudio left alone under a live recording
        finally:
            rec.stop()
        assert voice.refresh_devices() is True

    def test_survives_a_failing_rescan(self, _inject):
        sd = _inject()

        def _boom():
            raise RuntimeError("PortAudio busy")

        sd._terminate = _boom
        assert voice.refresh_devices() is False


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class TestRecorder:
    def test_records_and_returns_valid_wav(self, _inject):
        _inject()
        rec = voice.Recorder()
        assert rec._stream.started is True
        rec._callback(_FakeNdarray(b"\x01\x00"), 1, None, None)
        rec._callback(_FakeNdarray(b"\x02\x00"), 1, None, None)
        wav_bytes = rec.stop()
        assert rec._stream.closed is True
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnchannels() == voice.CHANNELS
            assert wf.getframerate() == voice.SAMPLE_RATE
            assert wf.readframes(wf.getnframes()) == b"\x01\x00\x02\x00"

    def test_no_audio_returns_empty(self, _inject):
        _inject()
        assert voice.Recorder().stop() == b""

    def test_opens_the_requested_device(self, _inject):
        _inject()
        rec = voice.Recorder(device=1)
        assert rec._stream.kwargs["device"] == 1
        assert rec.device_name == "Shure MV7 (USB)"
        rec.stop()

    def test_level_tracks_the_latest_block(self, _inject):
        _inject()
        rec = voice.Recorder()
        assert rec.level() == 0.0
        rec._callback(_FakeNdarray(b"\x01\x00", peak=16384), 1, None, None)
        assert rec.level() == 0.5  # exact in binary; pytest.approx needs real numpy
        rec._callback(_FakeNdarray(b"\x01\x00", peak=0), 1, None, None)
        assert rec.level() == 0.0
        rec.stop()


class TestRecorderFormatNegotiation:
    """A mic that refuses 16 kHz mono must still record, at its own format."""

    def test_falls_back_to_the_device_default_format(self, _inject):
        _inject(reject=lambda kw: kw.get("samplerate") == voice.SAMPLE_RATE)
        rec = voice.Recorder(device=1)  # Shure: 44100 Hz, 2 ch
        assert (rec.samplerate, rec.channels) == (44100, 2)
        assert rec._stream.kwargs["samplerate"] == 44100
        rec.stop()

    def test_negotiated_format_is_written_into_the_wav(self, _inject):
        _inject(reject=lambda kw: kw.get("samplerate") == voice.SAMPLE_RATE)
        rec = voice.Recorder(device=1)
        rec._callback(_FakeNdarray(b"\x01\x00\x02\x00"), 1, None, None)
        with wave.open(io.BytesIO(rec.stop()), "rb") as wf:
            assert wf.getframerate() == 44100
            assert wf.getnchannels() == 2

    def test_caps_the_fallback_at_two_channels(self, _inject):
        _inject(
            devices=[
                {
                    "name": "Big Interface",
                    "max_input_channels": 18,
                    "max_output_channels": 0,
                    "default_samplerate": 48000.0,
                }
            ],
            reject=lambda kw: kw.get("samplerate") == voice.SAMPLE_RATE,
        )
        rec = voice.Recorder(device=0)
        assert rec.channels == 2  # 18-channel interfaces would waste 16 of them
        rec.stop()

    def test_reraises_when_there_is_nothing_else_to_try(self, _inject):
        _inject(
            devices=[{"name": "Broken", "max_input_channels": 0, "max_output_channels": 0, "default_samplerate": 0}],
            reject=lambda kw: True,
        )
        with pytest.raises(Exception, match="Invalid sample rate"):
            voice.Recorder(device=0)

    def test_a_failed_open_does_not_leak_the_stream_count(self, _inject):
        _inject(reject=lambda kw: True)
        with pytest.raises(Exception, match="Invalid sample rate"):
            voice.Recorder()
        assert voice.refresh_devices() is True  # counter never incremented

    def test_a_failed_start_does_not_leak_the_stream_count(self, monkeypatch, _inject):
        """The count goes up *before* start() so a rescan cannot tear PortAudio
        down under a stream that exists but has not started — which means a
        start() that raises has to put it back, or every later rescan is blocked
        for the life of the process."""
        sd = _inject()

        def _no_start(self):
            raise OSError("could not start stream")

        opened = []
        real_init = sd.InputStream.__init__

        def _track(self, **kwargs):
            real_init(self, **kwargs)
            opened.append(self)

        monkeypatch.setattr(sd.InputStream, "__init__", _track)
        monkeypatch.setattr(sd.InputStream, "start", _no_start)
        with pytest.raises(OSError, match="could not start"):
            voice.Recorder()
        assert voice._open_streams == 0
        assert voice.refresh_devices() is True
        assert opened and all(s.closed for s in opened)  # and the stream is not leaked

    def test_a_live_stream_still_blocks_a_rescan(self, _inject):
        """The counter moved earlier in __init__; the guard must still hold."""
        _inject()
        recorder = voice.Recorder()
        try:
            assert voice.refresh_devices() is False
        finally:
            recorder.stop()
        assert voice.refresh_devices() is True


# ---------------------------------------------------------------------------
# transcribe / model cache
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_empty_bytes_short_circuits(self):
        assert voice.transcribe(b"") == ""

    def test_transcribes_locally(self, monkeypatch, _inject):
        monkeypatch.setenv("VOICE_MODEL", "base")
        captured: dict = {}
        _inject(faster_whisper_captured=captured)
        assert voice.is_model_loaded() is False
        result = voice.transcribe(_wav_bytes())
        assert result == "hello world"
        assert captured["size"] == "base"
        assert captured["device"] == "cpu"
        # Model is cached after first use.
        assert voice.is_model_loaded() is True

    def test_model_reused_across_calls(self, monkeypatch, _inject):
        monkeypatch.setenv("VOICE_MODEL", "base")
        captured: dict = {}
        _inject(faster_whisper_captured=captured)
        voice.transcribe(_wav_bytes())
        first_model = voice._MODEL_CACHE["base"]
        voice.transcribe(_wav_bytes())
        assert voice._MODEL_CACHE["base"] is first_model  # not reloaded

    def test_conforming_audio_skips_conversion(self, monkeypatch, _inject):
        """16 kHz mono takes the identity path — no downmix, no resample."""
        _inject(faster_whisper_captured={})
        called = []
        monkeypatch.setattr(voice, "_resample", lambda *a: called.append("resample"))
        monkeypatch.setattr(voice, "_downmix", lambda *a: called.append("downmix"))
        assert voice.transcribe(_wav_bytes()) == "hello world"
        assert called == []


class TestResampling:
    """A mic that only speaks 48 kHz stereo must still transcribe.

    These use the *real* numpy (``numpy=False`` skips the fake), because the
    point is the arithmetic.
    """

    def test_48k_stereo_becomes_16k_mono(self, _inject):
        captured: dict = {}
        _inject(numpy=False, faster_whisper_captured=captured)
        assert voice.transcribe(_tone_wav(48000, 2)) == "hello world"
        # 0.05 s of audio at the model's rate, whatever it was recorded at.
        assert captured["n_samples"] == pytest.approx(16000 * 0.05, abs=2)

    def test_44k1_mono_becomes_16k(self, _inject):
        captured: dict = {}
        _inject(numpy=False, faster_whisper_captured=captured)
        voice.transcribe(_tone_wav(44100, 1))
        assert captured["n_samples"] == pytest.approx(16000 * 0.05, abs=2)

    def test_upsamples_from_8k(self, _inject):
        captured: dict = {}
        _inject(numpy=False, faster_whisper_captured=captured)
        voice.transcribe(_tone_wav(8000, 1))
        assert captured["n_samples"] == pytest.approx(16000 * 0.05, abs=2)

    def test_downmix_averages_channels(self, _inject):
        _inject(numpy=False)
        mono = voice._downmix([1.0, 3.0, 2.0, 4.0], 2)
        assert list(mono) == [2.0, 3.0]

    def test_downmix_drops_a_torn_trailing_frame(self, _inject):
        _inject(numpy=False)
        assert len(voice._downmix([1.0, 3.0, 2.0], 2)) == 1

    def test_resample_of_too_short_audio_is_empty(self, _inject):
        _inject(numpy=False)
        assert len(voice._resample([1.0], 48000, 16000)) == 0


class TestTranscribeMedia:
    """Browser MediaRecorder blobs (webm/mp4) go straight to the model's PyAV decoder."""

    def test_empty_bytes_short_circuits(self):
        assert voice.transcribe_media(b"") == ""

    def test_passes_bytesio_to_model(self, monkeypatch):
        captured: dict = {}

        class _Segment:
            def __init__(self, text):
                self.text = text

        class _Model:
            def transcribe(self, audio, beam_size=None):
                captured["audio"] = audio
                captured["beam_size"] = beam_size
                return ([_Segment(" who "), _Segment(" said what ")], object())

        monkeypatch.setattr(voice, "_get_model", lambda: _Model())
        assert voice.transcribe_media(b"\x1aE\xdf\xa3fake-webm") == "who said what"
        # The blob must reach the model as a file-like object so faster-whisper's
        # PyAV path sniffs the container format (webm/opus, Safari mp4).
        assert isinstance(captured["audio"], io.BytesIO)
        assert captured["audio"].getvalue() == b"\x1aE\xdf\xa3fake-webm"
        assert captured["beam_size"] == 5

    def test_decode_error_propagates(self, monkeypatch):
        class _Model:
            def transcribe(self, audio, beam_size=None):
                raise ValueError("cannot decode container")

        monkeypatch.setattr(voice, "_get_model", lambda: _Model())
        with pytest.raises(ValueError, match="cannot decode"):
            voice.transcribe_media(b"garbage-bytes")


# ---------------------------------------------------------------------------
# record_voice_input (TUI overlay)
# ---------------------------------------------------------------------------


class _FakeLive:
    def __init__(self):
        self.frames = []

    def update(self, renderable):
        self.frames.append(renderable)


def _console():
    from rich.console import Console

    return Console(file=io.StringIO(), width=80)


def _render(panel, width: int = 100) -> str:
    """Render a Panel to plain text for screen assertions."""
    from rich.console import Console

    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False, color_system=None).print(panel)
    return buf.getvalue()


def _stub_render(**_kwargs):
    """Stand-in for the picker's own _render, which the mic test paints through."""
    return Text("")


class _KeySequence:
    def __init__(self, keys):
        self._keys = list(keys)

    def __call__(self, timeout=None):
        return self._keys.pop(0) if self._keys else ""


class TestDoubleTapSpace:
    def _d(self, threshold=0.30):
        from yeaboi.ui.shared._voice_input import DoubleTapSpace

        return DoubleTapSpace(threshold=threshold)

    def test_first_space_is_not_double(self):
        assert self._d().is_double(prev_char_is_space=False, now=1.0) is False

    def test_quick_second_space_triggers(self):
        d = self._d()
        d.is_double(prev_char_is_space=False, now=1.0)  # first tap inserts a space
        assert d.is_double(prev_char_is_space=True, now=1.1) is True

    def test_slow_second_space_does_not_trigger(self):
        d = self._d(threshold=0.30)
        d.is_double(prev_char_is_space=False, now=1.0)
        assert d.is_double(prev_char_is_space=True, now=1.6) is False

    def test_requires_prev_char_to_be_space(self):
        # Cursor moved between taps → char before cursor isn't the inserted space.
        d = self._d()
        d.is_double(prev_char_is_space=False, now=1.0)
        assert d.is_double(prev_char_is_space=False, now=1.1) is False

    def test_no_immediate_retrigger_after_double(self):
        d = self._d()
        d.is_double(prev_char_is_space=False, now=1.0)
        assert d.is_double(prev_char_is_space=True, now=1.1) is True
        assert d.is_double(prev_char_is_space=True, now=1.15) is False


class TestDoubleTapInDescriptionLoop:
    """End-to-end wiring: double-tap Space in the description loop dictates."""

    def test_double_tap_space_triggers_dictation(self, monkeypatch):
        from yeaboi.ui.session.phases import _phases_intake

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        # The strict probe too, not just the cheap one: record_voice_input gates
        # on probe_voice_backend, so on a machine without the voice extra — every
        # CI runner — this test would otherwise fall into the install offer and
        # its leftover "enter" would accept a real 325 MB install.
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (True, ""))

        class _Rec:
            def __init__(self, **kwargs):
                self.device_name = "Fake Mic"

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda wav: "four developers")

        # Type "Hi", a space, then a second quick space (double-tap) → records;
        # "z" stops recording; transcript inserts; Enter submits.
        keys = iter(["H", "i", " ", " ", "z", "enter"])

        def _key(timeout=None):
            return next(keys, "")

        result = _phases_intake._phase_description_input(_FakeLive(), _console(), _key)
        assert result is not None
        desc = result[0]
        assert "four developers" in desc
        # The first space is kept as a separator; the gesture's 2nd space is not.
        assert desc.strip() == "Hi four developers"


class TestInputBoxTitle:
    """The mic chip in the input box title — the one place nothing crops."""

    @pytest.fixture(autouse=True)
    def _reset_chip(self):
        from yeaboi.ui.shared import _voice_input

        _voice_input.reset_voice_chip()
        yield
        _voice_input.reset_voice_chip()

    def test_advertises_the_gesture_when_installed(self, monkeypatch):
        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setattr(voice, "voice_state", lambda: "ready")
        assert "Space Space" in input_box_title("Message", 60).plain

    def test_keeps_the_gesture_when_voice_is_merely_installable(self, monkeypatch):
        """The chip must not deny a feature that is one keystroke away: the
        double-tap works, it just opens the install offer first."""
        from yeaboi.ui.shared._voice_input import input_box_title, voice_chip

        monkeypatch.setattr(voice, "voice_state", lambda: "installable")
        assert "Space Space" in input_box_title("Message", 60).plain
        assert voice_chip()[1] == "rgb(80,80,92)"  # dimmed: live, not yet set up

    @pytest.mark.parametrize("state", ["declined", "unsupported"])
    def test_says_off_only_when_dictation_really_is_off(self, monkeypatch, state):
        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setattr(voice, "voice_state", lambda: state)
        title = input_box_title("Message", 60).plain
        assert "off" in title
        assert "Space Space" not in title

    def test_the_two_live_states_are_the_same_width(self, monkeypatch):
        """input_box_title and the standup box-top both measure this chip; a
        width change between the states would move a border."""
        from rich.cells import cell_len

        from yeaboi.ui.shared._voice_input import reset_voice_chip, voice_chip

        monkeypatch.setattr(voice, "voice_state", lambda: "ready")
        ready = cell_len(voice_chip()[0])
        reset_voice_chip()
        monkeypatch.setattr(voice, "voice_state", lambda: "installable")
        assert cell_len(voice_chip()[0]) == ready

    def test_ignores_the_tips_setting(self, monkeypatch):
        """An affordance is UI, not a tip — tips-off users must still see it."""
        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setenv("TIPS_ENABLED", "false")
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        assert "\U0001f3a4" in input_box_title("Message", 60).plain

    def test_drops_the_chip_when_the_box_is_too_narrow(self, monkeypatch):
        """Rich grows a Panel past its declared width for an oversized title."""
        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        assert input_box_title("Project Description", 30).plain == " Project Description "
        assert "Space Space" in input_box_title("Project Description", 74).plain

    def test_unclamped_when_no_width_is_given(self, monkeypatch):
        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        assert "Space Space" in input_box_title("Message").plain

    def test_availability_is_probed_once(self, monkeypatch):
        """Titles rebuild every frame; is_voice_available walks sys.path twice."""
        from yeaboi.ui.shared._voice_input import input_box_title

        calls = []
        monkeypatch.setattr(voice, "is_voice_available", lambda: (calls.append(1), (True, ""))[1])
        for _ in range(5):
            input_box_title("Message", 60)
        assert len(calls) == 1

    def test_the_chip_never_widens_the_panel(self, monkeypatch):
        """The box is padded into a fixed column; an over-wide title would ragged it."""
        import rich.box
        from rich.cells import cell_len
        from rich.panel import Panel
        from rich.text import Text

        from yeaboi.ui.shared._voice_input import input_box_title

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        for box_w in (40, 50, 60, 74, 90):
            panel = Panel(
                Text("x"),
                title=input_box_title("Message", box_w),
                title_align="left",
                box=rich.box.ROUNDED,
                width=box_w,
                padding=(1, 2),
            )
            top = _render(panel, width=200).split("\n")[0]
            assert cell_len(top) == box_w, f"panel overflowed at width {box_w}"


class TestStandupBoxChip:
    """The standup field is hand-drawn, so its chip rides the box border."""

    @pytest.fixture(autouse=True)
    def _reset_chip(self):
        from yeaboi.ui.shared import _voice_input

        _voice_input.reset_voice_chip()
        yield
        _voice_input.reset_voice_chip()

    def _screen(self, monkeypatch, *, width: int, box_rows: int = 1, available=True) -> str:
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        reason = "" if available else "not installed"
        monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (available, reason))
        # The module check has to agree with is_voice_available, or the fake is
        # incoherent: "modules present but unavailable" is how a dead PortAudio
        # presents, and unsupported_blocker() reads it as beyond help rather
        # than as installable.
        monkeypatch.setattr("yeaboi.voice._module_check", lambda: (available, reason))
        # Pin the offer setting too: voice_state() reads it, and the chip must
        # not depend on whatever the ambient config happens to say.
        monkeypatch.setattr("yeaboi.config.is_voice_install_offer_enabled", lambda: True)
        monkeypatch.setattr("yeaboi.voice_install.unsupported_reason", lambda: "")
        return _render(
            _build_standup_input_screen("What did you do yesterday?", "", box_rows=box_rows, width=width, height=30),
            width=width,
        )

    def test_the_chip_rides_the_border_not_the_label(self, monkeypatch):
        """On the label it would sit at the tail of a no_wrap/ellipsis line —
        the exact position the chip was moved off in the first place."""
        out = self._screen(monkeypatch, width=100)
        chip_line = next(line for line in out.splitlines() if "Space Space" in line)
        assert "╭" in chip_line  # it is the box's top border
        assert "What did you do yesterday?" not in chip_line  # not the prompt label

    def test_the_chip_survives_a_narrow_terminal(self, monkeypatch):
        assert "Space Space" in self._screen(monkeypatch, width=72)

    def test_large_box_gets_the_chip_too(self, monkeypatch):
        out = self._screen(monkeypatch, width=100, box_rows=4)
        chip_line = next(line for line in out.splitlines() if "Space Space" in line)
        assert "╭" in chip_line

    def test_keeps_the_gesture_when_voice_is_installable(self, monkeypatch):
        assert "Space Space" in self._screen(monkeypatch, width=100, available=False)

    def test_says_off_once_dictation_is_really_unavailable(self, monkeypatch):
        monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "unsupported")
        assert "🎤 off" in self._screen(monkeypatch, width=100, available=False)

    def test_the_inlaid_border_keeps_the_box_square(self, monkeypatch):
        """The chip eats border dashes, so a mis-counted width (the emoji is two
        cells) would leave the top edge longer or shorter than the bottom."""
        from rich.cells import cell_len

        out = self._screen(monkeypatch, width=100)
        top = next(line for line in out.splitlines() if "Space Space" in line)
        bottom = next(line for line in out.splitlines() if "╰" in line)
        assert cell_len(top[top.index("╭") : top.index("╮") + 1]) == cell_len(
            bottom[bottom.index("╰") : bottom.index("╯") + 1]
        )


class TestVoiceDevicePicker:
    """Settings → Voice Input → Input Device."""

    DEVICES = [
        {"index": 0, "name": "MacBook Pro Microphone", "channels": 1, "samplerate": 48000, "is_default": True},
        {"index": 1, "name": "Shure MV7 (USB)", "channels": 2, "samplerate": 44100, "is_default": False},
    ]

    def _state(self, sel: int = 0) -> dict:
        return {"devices": self.DEVICES, "sel": sel}

    def test_arrows_move_and_wrap(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import voice_picker_keypress

        state = self._state()
        assert voice_picker_keypress("down", state) == "none"
        assert state["sel"] == 1
        voice_picker_keypress("down", state)
        assert state["sel"] == 0  # wraps
        voice_picker_keypress("up", state)
        assert state["sel"] == 1

    def test_enter_selects_and_esc_cancels(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import voice_picker_keypress

        assert voice_picker_keypress("enter", self._state()) == "select"
        assert voice_picker_keypress(" ", self._state()) == "select"
        assert voice_picker_keypress("esc", self._state()) == "cancel"

    def test_t_tests_and_d_clears_to_the_system_default(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import voice_picker_keypress

        assert voice_picker_keypress("t", self._state()) == "test"
        assert voice_picker_keypress("d", self._state()) == "system"

    def test_unknown_keys_do_nothing(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import voice_picker_keypress

        state = self._state()
        assert voice_picker_keypress("x", state) == "none"
        assert state["sel"] == 0

    def test_arrows_survive_an_empty_device_list(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import voice_picker_keypress

        state = {"devices": [], "sel": 0}
        assert voice_picker_keypress("down", state) == "none"
        assert state["sel"] == 0

    def test_screen_lists_devices_and_marks_the_selection(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        out = _render(_build_voice_device_screen(self.DEVICES, 1, current="Shure MV7 (USB)", width=100, height=26))
        assert "MacBook Pro Microphone" in out
        assert "Shure MV7" in out
        assert "system default" in out
        assert "selected" in out
        assert "test mic" in out  # the hint row

    def test_screen_shows_a_level_meter_while_testing(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        idle = _render(_build_voice_device_screen(self.DEVICES, 0, width=100, height=26))
        testing = _render(_build_voice_device_screen(self.DEVICES, 0, width=100, height=26, testing=True, level=1.0))
        assert "▇" not in idle
        assert "▇" in testing
        assert "Speak now" in testing

    def test_screen_explains_an_empty_list(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        assert "No microphones detected" in _render(_build_voice_device_screen([], 0, width=100, height=26))

    def test_modal_returns_the_chosen_device(self, _inject):
        from yeaboi.ui.mode_select import _pick_voice_device

        _inject()
        keys = iter(["down", "enter"])
        assert (
            _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True)
            == "Shure MV7 (USB)"
        )

    def test_modal_esc_changes_nothing(self, _inject):
        from yeaboi.ui.mode_select import _pick_voice_device

        _inject()
        keys = iter(["esc"])
        assert _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True) is None

    def test_modal_d_clears_back_to_the_system_default(self, _inject):
        from yeaboi.ui.mode_select import _pick_voice_device

        _inject()
        keys = iter(["d"])
        assert _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True) == ""

    def test_modal_rescans_before_listing(self, _inject):
        """A mic plugged in mid-session is the whole reason this page exists."""
        from yeaboi.ui.mode_select import _pick_voice_device

        sd = _inject()
        keys = iter(["esc"])
        _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True)
        assert sd.calls == ["terminate", "initialize"]

    def test_modal_starts_on_the_configured_device(self, monkeypatch, _inject):
        from yeaboi.ui.mode_select import _pick_voice_device

        _inject()
        monkeypatch.setenv("VOICE_DEVICE", "MV7")
        keys = iter(["enter"])  # no movement — whatever is highlighted on open
        assert (
            _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True)
            == "Shure MV7 (USB)"
        )

    def test_screen_shows_a_notice(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        out = _render(
            _build_voice_device_screen(self.DEVICES, 0, width=100, height=26, notice="AirPods would not open")
        )
        assert "AirPods would not open" in out

    def test_screen_scrolls_a_long_device_list(self):
        """A host with a virtual audio driver can report a dozen inputs."""
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        many = [
            {"index": i, "name": f"Interface {i}", "channels": 1, "samplerate": 48000, "is_default": i == 0}
            for i in range(14)
        ]
        out = _render(_build_voice_device_screen(many, 13, width=100, height=26))
        assert "Interface 13" in out  # the selection is windowed into view
        assert "Interface 0" not in out  # …and the top has scrolled away
        assert "\u2503" in out  # the scrollbar thumb says there is more

    def test_screen_has_no_scrollbar_when_everything_fits(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen

        assert "\u2503" not in _render(_build_voice_device_screen(self.DEVICES, 0, width=100, height=26))

    def test_modal_enter_on_an_empty_list_does_not_save_anything(self, _inject):
        """Returning "" would confirm a choice the page just said cannot be made."""
        from yeaboi.ui.mode_select import _pick_voice_device

        _inject(devices=[{"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2}])
        keys = iter(["enter", "esc"])
        assert _pick_voice_device(_console(), _FakeLive(), lambda timeout=None: next(keys, "esc"), 0.05, True) is None


class TestMicrophoneTest:
    """Settings → Voice Input → Input Device → "t"."""

    DEVICE = {"index": 1, "name": "Shure MV7 (USB)", "channels": 2, "samplerate": 44100, "is_default": False}

    def test_runs_until_a_key_is_pressed_and_reports_no_problem(self, _inject):
        from yeaboi.ui.mode_select import _test_microphone

        _inject()
        keys = iter(["", "", "enter"])
        assert (
            _test_microphone(
                _console(), _FakeLive(), lambda timeout=None: next(keys, "enter"), 0.05, True, self.DEVICE, _stub_render
            )
            == ""
        )

    def test_does_not_buffer_audio(self, _inject):
        """Monitor mode: the page can sit open for minutes and keeps no take."""
        from yeaboi import voice

        _inject()
        recorder = voice.Recorder(device=1, monitor=True)
        recorder._callback(_FakeNdarray(b"\x01\x00", 1, peak=9000), 1, None, None)
        recorder._callback(_FakeNdarray(b"\x02\x00", 1, peak=9000), 1, None, None)
        assert recorder.level() > 0  # the meter still moves…
        assert recorder._frames == []  # …but nothing is retained
        assert recorder.stop() == b""

    def test_the_settings_test_uses_monitor_mode(self, monkeypatch, _inject):
        from yeaboi import voice
        from yeaboi.ui.mode_select import _test_microphone

        _inject()
        seen = {}
        real = voice.Recorder

        def _spy(**kwargs):
            seen.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(voice, "Recorder", _spy)
        keys = iter(["enter"])
        _test_microphone(
            _console(), _FakeLive(), lambda timeout=None: next(keys, "enter"), 0.05, True, self.DEVICE, _stub_render
        )
        assert seen["monitor"] is True

    def test_a_mic_that_will_not_open_returns_a_notice(self, monkeypatch, _inject):
        from yeaboi import voice
        from yeaboi.ui.mode_select import _test_microphone

        _inject()

        def _boom(**_kwargs):
            raise OSError("Device unavailable")

        monkeypatch.setattr(voice, "Recorder", _boom)
        notice = _test_microphone(
            _console(), _FakeLive(), lambda timeout=None: "enter", 0.05, True, self.DEVICE, _stub_render
        )
        assert "Shure MV7 (USB)" in notice
        assert "Device unavailable" in notice

    def test_a_mouse_event_does_not_end_the_test(self, _inject):
        from yeaboi.ui.mode_select import _test_microphone

        _inject()
        keys = iter(["\x1b[<0;10;5M", "enter"])
        assert (
            _test_microphone(
                _console(), _FakeLive(), lambda timeout=None: next(keys, "enter"), 0.05, True, self.DEVICE, _stub_render
            )
            == ""
        )

    def test_the_test_stops_the_recorder(self, monkeypatch, _inject):
        """The stream must not outlive the page — it would block every rescan."""
        from yeaboi import voice
        from yeaboi.ui.mode_select import _test_microphone

        _inject()
        _test_microphone(_console(), _FakeLive(), lambda timeout=None: "enter", 0.05, True, self.DEVICE, _stub_render)
        assert voice._open_streams == 0


class TestNextDevice:
    """Tab-to-switch, and the default-resolution it depends on."""

    def test_unset_preference_skips_past_the_system_default(self, _inject):
        """The bug this guards: VOICE_DEVICE unset means current is None, and a
        plain "not found → start at the top" lands back on the default itself —
        so the remedy the silence warning advertises would restart the take to
        move to the microphone the user was already on."""
        from yeaboi.ui.shared._voice_input import _next_device

        _inject()  # device 0 is the system default
        assert _next_device(None) == (1, "Shure MV7 (USB)")

    def test_advances_from_an_explicit_index(self, _inject):
        from yeaboi.ui.shared._voice_input import _next_device

        _inject()
        assert _next_device(1) == (0, "MacBook Pro Microphone")

    def test_wraps_around(self, _inject):
        from yeaboi.ui.shared._voice_input import _next_device

        _inject()
        assert _next_device(0) == (1, "Shure MV7 (USB)")

    def test_none_when_there_is_nothing_to_switch_to(self, _inject):
        from yeaboi.ui.shared._voice_input import _next_device

        _inject(devices=[_FAKE_DEVICES[0]])
        assert _next_device(None) is None

    def test_falls_back_to_the_top_when_no_default_is_reported(self, _inject):
        from yeaboi.ui.shared._voice_input import _next_device

        _inject(devices=[dict(d) for d in _FAKE_DEVICES], default_input=99)
        assert _next_device(None) == (0, "MacBook Pro Microphone")


class TestVoiceIndicator:
    def test_recording_shows_rec_and_stop_hint(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        border, line = voice_indicator("recording", 0.0, width=100)
        assert border.startswith("rgb(")
        assert "REC" in line
        assert "any key to stop" in line

    def test_recording_is_amber_not_the_error_red(self):
        """Red is this TUI's error colour — a red composer read as a crash."""
        from yeaboi.ui.shared._voice_input import _ERR_BORDER, voice_indicator

        for tick in (0.0, 0.2, 0.5, 0.9):
            border, _line = voice_indicator("recording", tick)
            assert border != _ERR_BORDER
            r, g, b = (int(v) for v in border.removeprefix("rgb(").removesuffix(")").split(","))
            assert g > 120 and r > g > b  # amber: warm, but nothing like (220,80,80)

    def test_recording_shows_elapsed_time_and_device(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        _border, line = voice_indicator("recording", 0.0, elapsed=64.0, device="Shure MV7", width=120)
        assert "1:04" in line
        assert "Shure MV7" in line

    def test_meter_follows_the_input_level(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        _b, quiet = voice_indicator("recording", 0.0, level=0.0, width=120)
        _b, loud = voice_indicator("recording", 0.0, level=1.0, width=120)
        assert quiet.count("▇") == 0
        assert loud.count("▇") == 8

    def test_silence_names_the_device_and_the_remedy(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        _b, line = voice_indicator("recording", 0.0, device="AirPods Pro", silent=True, width=120)
        assert "no sound from AirPods Pro" in line
        assert "Tab" in line

    def test_the_silent_form_always_keeps_a_way_out(self):
        """This is the branch a confused user lands on — it must never be the
        one that drops the exit affordance."""
        from yeaboi.ui.shared._voice_input import voice_indicator

        for width in (30, 45, 60, 80, 100, 140):
            _b, line = voice_indicator("recording", 0.0, device="A Very Long Device Name", silent=True, width=width)
            assert "Esc" in line, f"no way out at width {width}: {line!r}"

    def test_narrow_terminals_get_a_short_form(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        _b, narrow = voice_indicator("recording", 0.0, device="A Very Long Device Name", width=50)
        _b, mid = voice_indicator("recording", 0.0, device="A Very Long Device Name", width=80)
        _b, wide = voice_indicator("recording", 0.0, device="A Very Long Device Name", width=140)
        assert "A Very Long Device Name" not in narrow  # dropped, not cropped
        assert "A Very Long Device Name" in wide
        assert "any key stops" in narrow  # how to get out always survives
        assert len(narrow) < len(mid) < len(wide)

    def test_every_form_fits_the_width_it_was_given(self):
        from rich.cells import cell_len

        from yeaboi.ui.shared._voice_input import voice_indicator

        for width in range(40, 160, 7):
            for silent in (False, True):
                _b, line = voice_indicator(
                    "recording", 0.0, device="Scarlett 2i2 USB (2-in 2-out)", silent=silent, width=width
                )
                assert cell_len(line) <= width, f"{width}: {line!r}"

    def test_transcribing_has_spinner(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        border, line = voice_indicator("transcribing", 0.5)
        assert "Transcribing" in line
        assert border  # non-empty style

    def test_first_run_says_the_model_is_downloading(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        _border, line = voice_indicator("transcribing", 0.5, preparing=True)
        assert "downloads" in line

    def test_unknown_status_is_empty(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        assert voice_indicator("idle", 0.0) == ("", "")

    def test_recording_animates_with_tick(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        # Different ticks should vary the pulsing dot/border (animation).
        frames = {voice_indicator("recording", t) for t in (0.0, 0.2, 0.4, 0.6)}
        assert len(frames) > 1


class TestRecordVoiceInput:
    @pytest.fixture(autouse=True)
    def _strict_probe_follows_the_cheap_one(self, monkeypatch):
        """Keep the two availability probes telling the same story here.

        ``record_voice_input`` gates on the *strict* ``probe_voice_backend``,
        but most tests in this class only fake ``is_voice_available``. On a
        machine that has the voice extra the real strict probe succeeds anyway
        and the gap is invisible; on one that does not — every CI runner — the
        test falls straight into the install offer, records nothing, and asserts
        against an empty list. Delegating keeps them in step no matter which one
        a given test patches, and a test that patches the strict probe itself
        still wins by sharing this ``monkeypatch``.
        """
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: voice.is_voice_available())

    def _patch_voice(self, monkeypatch, *, available=(True, ""), transcript="hello", frames_have_audio=True):
        from yeaboi.ui.shared import _voice_input

        monkeypatch.setattr(voice, "is_voice_available", lambda: available)
        # record_voice_input asks the strict probe, not the per-frame one.
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: available)

        class _Rec:
            def __init__(self, **kwargs):
                self.device_name = "Fake Mic"

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO" if frames_have_audio else b""

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda wav: transcript)
        return _voice_input

    def test_returns_transcript(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, transcript="build a todo app")
        live = _FakeLive()
        result = mod.record_voice_input(live, _console(), _KeySequence(["", "enter"]))
        assert result == "build a todo app"
        assert live.frames

    def test_esc_cancels(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, transcript="ignored")
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["esc"])) is None

    def test_unavailable_offers_to_install_and_returns_none_on_decline(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, available=(False, "Install voice extra: uv sync --extra voice"))
        mod.reset_voice_chip()  # also clears the session decline
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["esc"])) is None

    def test_no_audio_returns_none(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, frames_have_audio=False)
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["", "enter"])) is None

    def test_empty_transcript_returns_none(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, transcript="")
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["enter"])) is None

    def test_pauses_and_resumes_music_around_recording(self, monkeypatch):
        # Background music must duck while recording, then come back.
        from yeaboi import music

        events = []
        monkeypatch.setattr(music, "pause_for_voice", lambda: events.append("pause"))
        monkeypatch.setattr(music, "resume_after_voice", lambda: events.append("resume"))
        mod = self._patch_voice(monkeypatch, transcript="hi")
        mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["", "enter"]))
        assert events == ["pause", "resume"]

    def test_render_status_receives_a_finished_border_and_line(self, monkeypatch):
        """Screens no longer compute the indicator — the loop hands them the pair."""
        mod = self._patch_voice(monkeypatch, transcript="hi")
        seen: list[tuple[str, str]] = []

        def _render(border, line):
            seen.append((border, line))
            return "frame"

        wide = Console(file=io.StringIO(), width=140)
        mod.record_voice_input(_FakeLive(), wide, _KeySequence(["", "enter"]), render_status=_render)
        assert seen
        assert all(border.startswith("rgb(") for border, _ in seen)
        assert any("REC" in line for _, line in seen)
        assert any("Fake Mic" in line for _, line in seen)  # the mic that opened is named

    def test_mic_failure_names_the_device_and_the_remedy(self, monkeypatch, _inject):
        """ "Could not access microphone" was the same message for every cause."""
        from yeaboi.ui.shared._voice_input import _mic_error

        _inject()
        monkeypatch.setenv("VOICE_DEVICE", "MV7")
        message = _mic_error(RuntimeError("Invalid number of channels [PaErrorCode -9998]"))
        assert "Shure MV7 (USB)" in message
        assert "PaErrorCode" in message  # the real reason, not a generic one
        assert "Settings" in message  # and where to fix it

    def test_mic_failure_falls_back_to_the_exception_type(self, _inject):
        from yeaboi.ui.shared._voice_input import _mic_error

        _inject()
        assert "OSError" in _mic_error(OSError())

    def test_tab_switches_to_the_next_microphone(self, monkeypatch):
        from yeaboi.ui.shared import _voice_input

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        monkeypatch.setattr(voice, "resolve_device", lambda *a: 0)
        monkeypatch.setattr(
            voice,
            "list_input_devices",
            lambda: [
                {"index": 0, "name": "Built-in", "channels": 1, "samplerate": 48000, "is_default": True},
                {"index": 1, "name": "Shure MV7", "channels": 2, "samplerate": 44100, "is_default": False},
            ],
        )
        opened: list[int | None] = []

        class _Rec:
            def __init__(self, device=None, **kwargs):
                opened.append(device)
                self.device_name = f"device {device}"

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda wav: "after the switch")
        result = _voice_input.record_voice_input(_FakeLive(), _console(), _KeySequence(["tab", "enter"]))
        assert opened == [0, 1]  # restarted on the next device
        assert result == "after the switch"

    def test_a_dead_second_mic_falls_back_to_the_previous_one(self, monkeypatch):
        """Tab onto a busy mic must not end the session — the take is already
        gone, but the user keeps a working microphone and can carry on."""
        from yeaboi.ui.shared import _voice_input

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        monkeypatch.setattr(voice, "resolve_device", lambda *a: 0)
        monkeypatch.setattr(
            voice,
            "list_input_devices",
            lambda: [
                {"index": 0, "name": "Built-in", "channels": 1, "samplerate": 48000, "is_default": True},
                {"index": 1, "name": "Busy USB", "channels": 2, "samplerate": 44100, "is_default": False},
            ],
        )
        opened: list[int | None] = []

        class _Rec:
            def __init__(self, device=None, **kwargs):
                opened.append(device)
                if device == 1:
                    raise OSError("Device busy")
                self.device_name = f"device {device}"

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda wav: "kept going")
        result = _voice_input.record_voice_input(_FakeLive(), _console(), _KeySequence(["tab", "enter"]))
        assert opened == [0, 1, 0]  # tried the next mic, then reopened the previous
        assert result == "kept going"  # …and the user was not dropped back to the screen

    def test_both_mics_dead_gives_up_cleanly(self, monkeypatch):
        from yeaboi import music
        from yeaboi.ui.shared import _voice_input

        events = []
        monkeypatch.setattr(music, "pause_for_voice", lambda: events.append("pause"))
        monkeypatch.setattr(music, "resume_after_voice", lambda: events.append("resume"))
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        monkeypatch.setattr(voice, "resolve_device", lambda *a: 0)
        monkeypatch.setattr(
            voice,
            "list_input_devices",
            lambda: [
                {"index": 0, "name": "Built-in", "channels": 1, "samplerate": 48000, "is_default": True},
                {"index": 1, "name": "Busy USB", "channels": 2, "samplerate": 44100, "is_default": False},
            ],
        )
        calls = {"n": 0}

        class _Rec:
            def __init__(self, device=None, **kwargs):
                calls["n"] += 1
                if calls["n"] > 1:  # the switch and the fallback both fail
                    raise OSError("Device busy")
                self.device_name = f"device {device}"

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        assert _voice_input.record_voice_input(_FakeLive(), _console(), _KeySequence(["tab", "x"])) is None
        assert events == ["pause", "resume"]  # music restored even so

    def test_tab_is_a_no_op_with_only_one_microphone(self, monkeypatch):
        from yeaboi.ui.shared import _voice_input

        mod = self._patch_voice(monkeypatch, transcript="unchanged")
        monkeypatch.setattr(
            voice,
            "list_input_devices",
            lambda: [{"index": 0, "name": "Built-in", "channels": 1, "samplerate": 48000, "is_default": True}],
        )
        assert mod is _voice_input
        assert _voice_input.record_voice_input(_FakeLive(), _console(), _KeySequence(["tab", "enter"])) == "unchanged"

    def test_resumes_music_when_mic_fails(self, monkeypatch):
        from yeaboi import music
        from yeaboi.ui.shared import _voice_input

        events = []
        monkeypatch.setattr(music, "pause_for_voice", lambda: events.append("pause"))
        monkeypatch.setattr(music, "resume_after_voice", lambda: events.append("resume"))
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))

        def _boom(*args, **kwargs):
            raise RuntimeError("no mic")

        monkeypatch.setattr(voice, "Recorder", _boom)
        _voice_input.record_voice_input(_FakeLive(), _console(), _KeySequence(["x"]))
        assert events == ["pause", "resume"]  # music restored even on mic failure


class TestProbeVoiceBackend:
    """The cheap probe answers "are the modules there?"; the strict one answers
    "will recording actually work?" — which on Linux is a different question."""

    def setup_method(self):
        voice.reset_probe()

    def teardown_method(self):
        voice.reset_probe()

    def test_a_present_but_unusable_portaudio_is_caught(self, monkeypatch, _inject):
        sd = _inject(faster_whisper_captured={})

        def boom():
            raise OSError("PortAudio library not found")

        monkeypatch.setattr(sd, "query_devices", boom)
        assert voice.probe_voice_backend()[0] is False

    def test_the_cheap_probe_stays_cheap_and_never_opens_portaudio(self, monkeypatch, _inject):
        sd = _inject(faster_whisper_captured={})
        calls = []
        monkeypatch.setattr(sd, "query_devices", lambda *a, **k: calls.append(1) or [])
        assert voice.is_voice_available() == (True, "")
        assert calls == []

    def test_the_cheap_probe_reports_a_strict_failure_once_it_is_known(self, monkeypatch, _inject):
        sd = _inject(faster_whisper_captured={})
        monkeypatch.setattr(sd, "query_devices", lambda *a, **k: (_ for _ in ()).throw(OSError("no lib")))
        assert voice.is_voice_available()[0] is True  # modules are on the path
        voice.probe_voice_backend()
        assert voice.is_voice_available()[0] is False  # ...but the backend is dead

    def test_the_strict_probe_runs_once_per_process(self, monkeypatch, _inject):
        sd = _inject(faster_whisper_captured={})
        calls = []
        monkeypatch.setattr(sd, "query_devices", lambda *a, **k: calls.append(1) or [])
        voice.probe_voice_backend()
        voice.probe_voice_backend()
        assert len(calls) == 1
        voice.probe_voice_backend(force=True)
        assert len(calls) == 2

    def test_reset_probe_clears_the_cache(self, monkeypatch, _inject):
        sd = _inject(faster_whisper_captured={})
        calls = []
        monkeypatch.setattr(sd, "query_devices", lambda *a, **k: calls.append(1) or [])
        voice.probe_voice_backend()
        voice.reset_probe()
        voice.probe_voice_backend()
        assert len(calls) == 2

    def test_missing_modules_short_circuit_before_any_import(self, _inject):
        _inject(sounddevice=False)
        assert voice.probe_voice_backend()[0] is False

    def test_render_paths_never_call_the_strict_probe(self, monkeypatch):
        """A regression guard: this probe costs ~100 ms and opens PortAudio, and
        the chip and tips are rebuilt on every frame."""
        from yeaboi.ui.shared import _tips, _voice_input

        def forbidden(**_kw):
            raise AssertionError("probe_voice_backend must never run on a render path")

        monkeypatch.setattr(voice, "probe_voice_backend", forbidden)
        _voice_input.reset_voice_chip()
        _tips.get_tips.cache_clear()
        _voice_input.voice_chip()
        _tips.get_tips()
        _tips.get_tips.cache_clear()


class TestVoiceState:
    def _state(self, monkeypatch, *, available, unsupported="", offer=True):
        from yeaboi import voice_install

        monkeypatch.setattr(voice, "is_voice_available", lambda: (available, ""))
        monkeypatch.setattr(voice_install, "unsupported_reason", lambda: unsupported)
        monkeypatch.setattr("yeaboi.config.is_voice_install_offer_enabled", lambda: offer)
        return voice.voice_state()

    def test_ready(self, monkeypatch):
        assert self._state(monkeypatch, available=True) == "ready"

    def test_installable(self, monkeypatch):
        assert self._state(monkeypatch, available=False) == "installable"

    def test_declined(self, monkeypatch):
        assert self._state(monkeypatch, available=False, offer=False) == "declined"

    def test_unsupported_beats_declined(self, monkeypatch):
        assert self._state(monkeypatch, available=False, unsupported="musl libc", offer=False) == "unsupported"

    def test_an_installed_machine_is_ready_even_if_the_offer_is_off(self, monkeypatch):
        assert self._state(monkeypatch, available=True, offer=False) == "ready"


class TestInstallStatusLine:
    """Render tests for the setup frames. No new _build_*_screen is introduced —
    these lines travel through the callers' existing status row, so what has to
    be proven is that they survive _fit at every width the app supports."""

    from yeaboi.ui.shared import _voice_input as _mod

    STAGES = (
        ("install", {"detail": "downloading ctranslate2 (37 MB)", "elapsed": 7.0}),
        ("download", {"fraction": 0.52, "size": "76/145 MB"}),
        ("download", {"fraction": None}),
        ("load", {}),
        ("ready", {}),
    )

    def test_every_stage_returns_a_colour_and_a_line(self):
        from yeaboi.ui.shared._voice_input import install_status_line

        for stage, kwargs in self.STAGES:
            border, line = install_status_line(stage, tick=1.0, width=100, **kwargs)
            assert border.startswith("rgb("), stage
            assert line.strip(), stage

    @pytest.mark.parametrize("width", [100, 68, 40, 28, 20])
    def test_lines_fit_the_width_they_are_given(self, width):
        from rich.cells import cell_len

        from yeaboi.ui.shared._voice_input import install_offer_line, install_status_line

        assert cell_len(install_offer_line(size_mb=325, width=width)) <= width
        for stage, kwargs in self.STAGES:
            _border, line = install_status_line(stage, tick=1.0, width=width, **kwargs)
            assert cell_len(line) <= width, (stage, width, line)

    @pytest.mark.parametrize("width", [100, 68, 40, 28, 20])
    def test_the_offer_never_loses_its_two_answers(self, width):
        """Whatever else drops, the accept key and the way out survive."""
        from yeaboi.ui.shared._voice_input import install_offer_line

        line = install_offer_line(size_mb=325, width=width)
        assert "Enter" in line and "Esc" in line

    @pytest.mark.parametrize("width", [100, 68, 40])
    def test_cancellable_stages_keep_saying_so(self, width):
        from yeaboi.ui.shared._voice_input import install_status_line

        for stage, kwargs in self.STAGES:
            if stage == "ready":
                continue
            _border, line = install_status_line(stage, tick=1.0, width=width, can_cancel=True, **kwargs)
            assert "Esc" in line, (stage, width)

    def test_a_blocking_key_reader_never_promises_an_esc(self):
        """Advertising a key that physically cannot fire is worse than silence."""
        from yeaboi.ui.shared._voice_input import install_status_line

        for stage, kwargs in self.STAGES:
            _border, line = install_status_line(stage, tick=1.0, width=200, can_cancel=False, **kwargs)
            assert "Esc" not in line, stage

    def test_the_offer_size_is_computed_not_hardcoded(self):
        from yeaboi.ui.shared._voice_input import install_offer_line

        assert "~3280 MB" in install_offer_line(size_mb=3280, width=200)

    def test_the_reinstall_wording_explains_the_regression(self):
        from yeaboi.ui.shared._voice_input import install_offer_line

        assert "upgrade removed" in install_offer_line(size_mb=180, reinstall=True, width=200)

    @pytest.mark.parametrize(
        ("fraction", "expected"),
        [(0.0, "▱" * 10), (0.52, "▰" * 5 + "▱" * 5), (1.0, "▰" * 10), (-1.0, "▱" * 10), (2.0, "▰" * 10)],
    )
    def test_bar_clamps(self, fraction, expected):
        from yeaboi.ui.shared._voice_input import _bar

        assert _bar(fraction) == expected

    def test_the_popup_fallback_does_not_wrap_at_eighty_columns(self):
        """_center's vertical centring assumes a five-row popup; a wrapped line
        breaks it."""
        from yeaboi.ui.shared._voice_input import _REC_BORDER, _center, install_offer_line

        console = Console(file=io.StringIO(), width=80)
        line = install_offer_line(size_mb=325, width=_status_width_for(console))
        rendered = _render(_center(console, line, _REC_BORDER), width=80)
        body = [row for row in rendered.splitlines() if "Set up dictation" in row]
        assert len(body) == 1  # one row, not a wrapped two
        assert "n never" in body[0]  # ...and not truncated either


def _status_width_for(console):
    from yeaboi.ui.shared._voice_input import _status_width

    return _status_width(console)


class TestVoiceInstallOffer:
    """The double-tap-Space path when the packages are missing."""

    def _setup(self, monkeypatch, *, state="installable", plan_blocked="", install=(True, ""), model=(True, "")):
        from yeaboi import voice_install
        from yeaboi.ui.shared import _voice_input

        _voice_input.reset_voice_chip()  # clears the session decline too
        monkeypatch.setattr(voice, "voice_state", lambda: state)
        monkeypatch.setattr(voice_install, "unsupported_reason", lambda: "musl libc" if state == "unsupported" else "")
        monkeypatch.setattr(voice_install, "size_estimate_mb", lambda: 325)
        monkeypatch.setattr(
            voice_install,
            "install_plan",
            lambda: voice_install.InstallPlan("pip", ("x",), "pip install x", True, plan_blocked, ""),
        )
        monkeypatch.setattr(voice_install, "install_packages", lambda *a, **k: install)
        monkeypatch.setattr(voice_install, "download_model", lambda *a, **k: model)
        monkeypatch.setattr(voice_install, "warm_model", lambda _s: (True, ""))
        monkeypatch.setattr("yeaboi.config.mark_voice_extra_installed", lambda: None)
        monkeypatch.setattr("yeaboi.config.voice_extra_was_installed", lambda: False)
        return _voice_input

    def _run(self, mod, keys, *, available_after=True, transcript="hello"):
        """Drive record_voice_input from unavailable through the offer."""
        states = iter([(False, "Install voice extra: pip install x")])

        def _probe(**_kw):
            return next(states, (available_after, "" if available_after else "still missing"))

        return _probe, mod.record_voice_input(_FakeLive(), _console(), _KeySequence(keys))

    def test_the_offer_is_shown_when_installable(self, monkeypatch):
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        seen: list[str] = []
        mod.record_voice_input(
            _FakeLive(),
            Console(file=io.StringIO(), width=140),
            _KeySequence(["esc"]),
            render_status=lambda _b, line: seen.append(line) or "frame",
        )
        assert any("Set up dictation" in line and "Enter installs" in line for line in seen)

    def test_enter_installs_and_falls_through_to_recording(self, monkeypatch):
        mod = self._setup(monkeypatch)
        probes = iter([(False, "nope")])
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: next(probes, (True, "")))

        class _Rec:
            device_name = "Fake Mic"

            def __init__(self, **_kw):
                pass

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda _wav: "spoken words")
        seen: list[str] = []
        result = mod.record_voice_input(
            _FakeLive(),
            Console(file=io.StringIO(), width=140),
            _KeySequence(["enter", "", "enter"]),
            render_status=lambda _b, line: seen.append(line) or "frame",
        )
        assert result == "spoken words"
        assert any("Dictation is ready" in line for line in seen)
        assert any("REC" in line for line in seen)

    def test_esc_declines_for_this_session_only(self, monkeypatch):
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        written: list[tuple] = []
        monkeypatch.setattr("yeaboi.config.set_config_value", lambda k, v: written.append((k, v)))

        first: list[str] = []
        mod.record_voice_input(
            _FakeLive(), _console(), _KeySequence(["esc"]), render_status=lambda _b, li: first.append(li) or "f"
        )
        second: list[str] = []
        mod.record_voice_input(
            _FakeLive(), _console(), _KeySequence(["x"]), render_status=lambda _b, li: second.append(li) or "f"
        )
        assert any("Set up dictation" in line for line in first)
        assert not any("Set up dictation" in line for line in second)
        assert written == []  # a session decline is never persisted

    def test_n_declines_permanently(self, monkeypatch):
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        recorded: list[bool] = []
        monkeypatch.setattr("yeaboi.config.set_voice_install_offer", lambda enabled: recorded.append(enabled))
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["n"])) is None
        assert recorded == [False]

    def test_space_tab_and_clicks_do_not_authorise_an_install(self, monkeypatch):
        """Key repeat on the double-tap must not agree to a 300 MB download."""
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        from yeaboi import voice_install

        started: list[int] = []
        monkeypatch.setattr(voice_install, "install_packages", lambda *a, **k: started.append(1) or (True, ""))
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence([" ", "tab", "click:4:9", "esc"])) is None
        assert started == []

    def test_an_unknown_key_keeps_the_offer_up(self, monkeypatch):
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        from yeaboi import voice_install

        started: list[int] = []
        monkeypatch.setattr(voice_install, "install_packages", lambda *a, **k: started.append(1) or (True, ""))
        seen: list[str] = []
        mod.record_voice_input(
            _FakeLive(),
            Console(file=io.StringIO(), width=140),
            _KeySequence(["q", "enter"]),
            render_status=lambda _b, li: seen.append(li) or "f",
        )
        assert started == [1]  # the typo did not dismiss the offer

    def test_install_failure_shows_the_manual_command(self, monkeypatch):
        mod = self._setup(monkeypatch, install=(False, "Install failed — run pip install x yourself"))
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["enter", "x"])) is None
        assert any("pip install x" in _render(frame) for frame in live.frames)

    def test_an_unsupported_platform_is_never_offered_an_install(self, monkeypatch):
        mod = self._setup(monkeypatch, state="unsupported")
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["x"])) is None
        rendered = " ".join(_render(frame) for frame in live.frames)
        assert "musl libc" in rendered
        assert "Set up dictation" not in rendered

    def test_a_blocked_plan_says_why_instead_of_offering(self, monkeypatch):
        mod = self._setup(monkeypatch, plan_blocked="`uv` is not on PATH")
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["x"])) is None
        assert "not on PATH" in " ".join(_render(frame) for frame in live.frames)

    def test_a_permanent_decline_still_shows_the_manual_command(self, monkeypatch):
        mod = self._setup(monkeypatch, state="declined")
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "run: pip install yeaboi[voice]"))
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["x"])) is None
        assert "pip install" in " ".join(_render(frame) for frame in live.frames)

    def test_a_failed_model_download_still_leaves_dictation_working(self, monkeypatch):
        """The packages are in; the model just downloads lazily as it always did."""
        mod = self._setup(monkeypatch, model=(False, "Can't reach huggingface.co"))
        probes = iter([(False, "nope")])
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: next(probes, (True, "")))

        class _Rec:
            device_name = "Fake Mic"

            def __init__(self, **_kw):
                pass

            def level(self):
                return 0.4

            def stop(self):
                return b"AUDIO"

        monkeypatch.setattr(voice, "Recorder", _Rec)
        monkeypatch.setattr(voice, "transcribe", lambda _wav: "still works")
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["enter", "", "enter"])) == "still works"

    def test_installed_but_still_invisible_asks_for_a_restart(self, monkeypatch):
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "Audio backend unavailable"))
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["enter", "x"])) is None
        assert "Audio backend unavailable" in " ".join(_render(frame) for frame in live.frames)

    def test_setting_up_dictation_never_touches_the_music(self, monkeypatch):
        """No microphone is open yet, and suspending someone's focus music for a
        multi-minute wait is backwards."""
        from yeaboi import music

        events: list[str] = []
        monkeypatch.setattr(music, "pause_for_voice", lambda: events.append("pause"))
        monkeypatch.setattr(music, "resume_after_voice", lambda: events.append("resume"))
        mod = self._setup(monkeypatch, install=(False, "nope"))
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        for keys in (["esc"], ["n"], ["enter", "x"]):
            mod.reset_voice_chip()
            mod.record_voice_input(_FakeLive(), _console(), _KeySequence(keys))
        assert events == []

    def test_the_frame_loop_is_throttled_even_by_a_non_blocking_reader(self, monkeypatch):
        """A reader that returns early must not spin the loop flat out for the
        whole install — measured at 2.1M frames in 44s before the floor."""
        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        from yeaboi import voice_install

        def _slow_install(_on_line, _cancel=None, **_kw):
            time.sleep(0.35)
            return True, ""

        monkeypatch.setattr(voice_install, "install_packages", _slow_install)
        painted: list[str] = []

        class _EagerKey:
            """Accepts a timeout and ignores it — the failure mode being guarded.

            Answers the offer at once, then returns "no key" instantly forever,
            which is what the install loop has to survive.
            """

            def __init__(self):
                self._first = True

            def __call__(self, timeout=None):
                if self._first:
                    self._first = False
                    return "enter"
                return ""

        mod.record_voice_input(
            _FakeLive(),
            _console(),
            _EagerKey(),
            render_status=lambda _b, line: painted.append(line) or "f",
        )
        # ~0.35s at 30fps is a handful of frames, not thousands.
        assert len(painted) < 100, len(painted)

    def test_the_offer_stays_above_the_music_pause(self):
        """The music invariant is structural: every early return from the offer
        sits above pause_for_voice, so there is nothing to resume."""
        from pathlib import Path

        source = Path(mod_source()).read_text(encoding="utf-8")
        assert source.index("_offer_install(live, console") < source.index("music.pause_for_voice()")

    def test_the_poker_duel_never_reaches_the_installer(self):
        """It runs headless on a server thread, with no screen to offer on."""
        from pathlib import Path

        import yeaboi.poker.server as poker_server

        assert "voice_install" not in Path(poker_server.__file__).read_text(encoding="utf-8")


def mod_source() -> str:
    from yeaboi.ui.shared import _voice_input

    return _voice_input.__file__


class TestInstallVoiceCommand:
    """`yeaboi --install-voice` — the headless twin of the in-app offer.

    This is the surface CI, dev containers and dumb terminals get, so its exit
    code is the whole contract: 0 means dictation actually runs here.
    """

    @pytest.fixture
    def _plan(self):
        from yeaboi import voice_install

        return voice_install.InstallPlan(
            method="pip",
            argv=("python", "-m", "pip", "install", "sounddevice"),
            display_command="python -m pip install sounddevice",
            durable=True,
            blocked="",
            follow_up="",
        )

    def test_reports_and_exits_1_when_the_platform_is_blocked(self, monkeypatch, capsys):
        from yeaboi import cli, voice_install

        blocked = voice_install.InstallPlan("blocked", (), "", False, "musl libc", "")
        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr(voice_install, "install_plan", lambda **_kw: blocked)
        assert cli._install_voice() == 1
        assert "musl libc" in capsys.readouterr().out

    def test_ignores_a_stored_verdict(self, monkeypatch):
        """Typing the command *is* the retry. A month-old cached failure — a
        wheel that had not landed, a mirror that had not synced — must not be
        the reason the explicit escape hatch refuses."""
        from yeaboi import cli, voice_install

        seen: dict = {}

        def _plan_spy(**kwargs):
            seen.update(kwargs)
            return voice_install.InstallPlan("blocked", (), "", False, "nope", "")

        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr(voice_install, "install_plan", _plan_spy)
        cli._install_voice()
        assert seen == {"ignore_verdict": True}

    def test_exits_1_when_the_install_fails(self, monkeypatch, capsys, _plan):
        from yeaboi import cli, voice_install

        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr(voice_install, "install_plan", lambda **_kw: _plan)
        monkeypatch.setattr(voice_install, "install_packages", lambda *_a, **_k: (False, "no wheel"))
        assert cli._install_voice() == 1
        assert "no wheel" in capsys.readouterr().out

    def test_happy_path_exits_0(self, monkeypatch, capsys, _plan):
        from yeaboi import cli, voice_install

        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr(voice_install, "install_plan", lambda **_kw: _plan)
        monkeypatch.setattr(voice_install, "install_packages", lambda *_a, **_k: (True, ""))
        monkeypatch.setattr("yeaboi.config.mark_voice_extra_installed", lambda: None)
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "download_model", lambda *_a, **_k: (True, ""))
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_k: (True, ""))
        assert cli._install_voice() == 0
        out = capsys.readouterr().out
        assert "Packages installed." in out
        assert "ready" in out

    def test_a_failed_model_download_still_probes(self, monkeypatch, capsys, _plan):
        """A missing model is a warning — it downloads lazily on first use. But
        returning 0 without probing would report success on a host where the
        packages landed and PortAudio is missing."""
        from yeaboi import cli, voice_install

        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr(voice_install, "install_plan", lambda **_kw: _plan)
        monkeypatch.setattr(voice_install, "install_packages", lambda *_a, **_k: (True, ""))
        monkeypatch.setattr("yeaboi.config.mark_voice_extra_installed", lambda: None)
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "download_model", lambda *_a, **_k: (False, "offline"))
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_k: (False, "Audio backend unavailable"))
        assert cli._install_voice() == 1
        out = capsys.readouterr().out
        assert "offline" in out
        assert "Audio backend unavailable" in out

    def test_skips_the_install_when_already_present(self, monkeypatch, capsys):
        from yeaboi import cli, voice_install

        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        monkeypatch.setattr(voice_install, "install_plan", _fail_if_called)
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: True)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_k: (True, ""))
        assert cli._install_voice() == 0
        assert "already installed" in capsys.readouterr().out


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("install_plan must not be consulted when voice is already available")


class TestInstallVoiceEcho:
    """The two plain-print helpers. No Rich here by design: the output has to
    survive a pipe, a CI log and a terminal with no cursor control."""

    def test_echo_once_drops_consecutive_repeats(self, capsys):
        from yeaboi import cli

        echoed: list[str] = []
        for phrase in ("Resolving", "Resolving", "Downloading", "Downloading", "Resolving"):
            cli._echo_once(echoed, phrase)
        assert capsys.readouterr().out.count("Resolving") == 2  # repeat collapsed, later recurrence kept
        assert echoed == ["Resolving", "Downloading", "Resolving"]

    def test_echo_once_ignores_the_empty_phrase(self, capsys):
        from yeaboi import cli

        echoed: list[str] = []
        cli._echo_once(echoed, "")
        assert capsys.readouterr().out == ""
        assert echoed == []

    def test_echo_progress_prints_a_percentage(self, capsys):
        from yeaboi import cli

        cli._echo_progress("120 MB / 145 MB", 0.83)
        assert "83%" in capsys.readouterr().out

    def test_echo_progress_stays_silent_without_a_fraction(self, capsys):
        """An unknown total is common early in a download; a bar with no number
        in it is worse than no line at all on a non-cursor terminal."""
        from yeaboi import cli

        cli._echo_progress("starting", None)
        assert capsys.readouterr().out == ""


class TestUnsupportedBlocker:
    """ "Installable" must mean an install would actually help.

    ``sounddevice`` is a pure-Python wheel that imports happily without the
    system PortAudio library, so on a Linux host missing ``libportaudio2`` the
    modules are present and an install exits 0 having changed nothing. Offering
    one would walk the user through ~325 MB and two minutes to arrive back
    exactly where they started — on the very platform the strict probe exists
    for.
    """

    @pytest.fixture(autouse=True)
    def _no_stored_verdict(self, monkeypatch):
        monkeypatch.setattr("yeaboi.voice_install.unsupported_reason", lambda: "")

    def test_missing_modules_stay_installable(self, monkeypatch):
        monkeypatch.setattr(voice, "_module_check", lambda: (False, "not installed"))
        monkeypatch.setattr(voice, "is_voice_available", lambda: (False, "not installed"))
        monkeypatch.setattr("yeaboi.config.is_voice_install_offer_enabled", lambda: True)
        assert voice.unsupported_blocker() == ""
        assert voice.voice_state() == "installable"

    def test_a_dead_backend_is_unsupported_not_installable(self, monkeypatch):
        monkeypatch.setattr(voice, "_module_check", lambda: (True, ""))
        monkeypatch.setattr(
            voice,
            "is_voice_available",
            lambda: (False, "Audio backend unavailable — sudo apt install libportaudio2"),
        )
        monkeypatch.setattr("yeaboi.config.is_voice_install_offer_enabled", lambda: True)
        assert "libportaudio2" in voice.unsupported_blocker()
        assert voice.voice_state() == "unsupported"

    def test_a_stored_verdict_wins(self, monkeypatch):
        monkeypatch.setattr("yeaboi.voice_install.unsupported_reason", lambda: "musl libc")
        monkeypatch.setattr(voice, "_module_check", lambda: (False, "not installed"))
        assert voice.unsupported_blocker() == "musl libc"

    def test_a_working_setup_has_no_blocker(self, monkeypatch):
        monkeypatch.setattr(voice, "_module_check", lambda: (True, ""))
        monkeypatch.setattr(voice, "is_voice_available", lambda: (True, ""))
        assert voice.unsupported_blocker() == ""
        assert voice.voice_state() == "ready"


class TestVoiceInstallModelStage:
    """What happens after the packages land — the half that runs in-process.

    Reuses the offer harness without inheriting its tests.
    """

    _setup = TestVoiceInstallOffer._setup

    def test_a_failed_download_never_warms_the_model(self, monkeypatch):
        """warm_model loads in-process, and WhisperModel downloads the weights
        itself when they are missing — with no progress, no byte count and no
        cancel. It would also defeat the reason the fetch runs in a child at
        all: on an AVX-less host that child dies of SIGILL, and importing
        ctranslate2 here would take the TUI down with the same instruction."""
        from yeaboi import voice_install

        mod = self._setup(monkeypatch, model=(False, "offline"))
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        monkeypatch.setattr(
            voice_install,
            "warm_model",
            lambda _s: pytest.fail("warm_model would download the weights in-process"),
        )
        mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["enter", "x"]))

    def test_a_successful_download_does_warm_the_model(self, monkeypatch):
        """The whole point of the extra stage: first dictation is instant."""
        from yeaboi import voice_install

        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))
        warmed: list[str] = []
        monkeypatch.setattr(voice_install, "warm_model", lambda s: warmed.append(s) or (True, ""))
        mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["enter", "x"]))
        assert len(warmed) == 1

    def test_an_unexpected_worker_error_is_caught_and_named(self, monkeypatch):
        """duck_working_thread does not catch, so without a guard the traceback
        prints straight through the Rich Live and the outcome stays at its
        default — "see the log", with nothing in the log."""
        from yeaboi import voice_install

        mod = self._setup(monkeypatch)
        monkeypatch.setattr(voice, "probe_voice_backend", lambda **_kw: (False, "nope"))

        def _boom(*_a, **_k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(voice_install, "install_packages", _boom)
        live = _FakeLive()
        assert mod.record_voice_input(live, _console(), _KeySequence(["enter", "x"])) is None
        assert any("unexpected error" in _render(frame) for frame in live.frames)
