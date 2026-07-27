"""Unit tests for voice input — mic recording, local Whisper transcription, overlay.

The audio/transcription packages (sounddevice, numpy, faster-whisper) are
optional and not installed in the test environment, so these tests inject fake
modules into sys.modules to exercise the lazy-import code paths. Transcription
runs locally (no API key), so there is nothing OpenAI-specific to mock.
"""

from __future__ import annotations

import importlib.machinery
import io
import sys
import types
import wave

import pytest

from yeaboi import voice
from yeaboi.config import get_voice_model

# ---------------------------------------------------------------------------
# Fakes for the optional dependencies
# ---------------------------------------------------------------------------


class _FakeNdarray:
    """Minimal stand-in for a numpy array covering the ops voice.py uses."""

    def __init__(self, data: bytes = b"", n: int = 0) -> None:
        self._data = data
        self._n = n

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


def _fake_sounddevice() -> types.ModuleType:
    mod = types.ModuleType("sounddevice")
    mod.InputStream = _FakeInputStream
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


@pytest.fixture
def _inject(monkeypatch):
    """Inject fake optional modules; returns a helper to toggle presence."""

    def install(*, numpy=True, sounddevice=True, faster_whisper_captured=None, segments=("  hello ", "world  ")):
        if numpy:
            monkeypatch.setitem(sys.modules, "numpy", _fake_numpy())
        if sounddevice:
            monkeypatch.setitem(sys.modules, "sounddevice", _with_spec(_fake_sounddevice()))
        else:
            monkeypatch.setitem(sys.modules, "sounddevice", None)
        if faster_whisper_captured is not None:
            monkeypatch.setitem(
                sys.modules, "faster_whisper", _with_spec(_fake_faster_whisper(faster_whisper_captured, segments))
            )

    return install


def _wav_bytes(pcm: bytes = b"\x01\x00\x02\x00") -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


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

        class _Rec:
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


class TestVoiceIndicator:
    def test_recording_has_red_border_and_stop_hint(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        border, line = voice_indicator("recording", 0.0)
        assert border.startswith("rgb(")
        assert "Recording" in line
        assert "any key to stop" in line

    def test_transcribing_has_spinner(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        border, line = voice_indicator("transcribing", 0.5)
        assert "Transcribing" in line
        assert border  # non-empty style

    def test_unknown_status_is_empty(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        assert voice_indicator("idle", 0.0) == ("", "")

    def test_recording_animates_with_tick(self):
        from yeaboi.ui.shared._voice_input import voice_indicator

        # Different ticks should vary the pulsing dot/border (animation).
        frames = {voice_indicator("recording", t) for t in (0.0, 0.2, 0.4, 0.6)}
        assert len(frames) > 1


class TestRecordVoiceInput:
    def _patch_voice(self, monkeypatch, *, available=(True, ""), transcript="hello", frames_have_audio=True):
        from yeaboi.ui.shared import _voice_input

        monkeypatch.setattr(voice, "is_voice_available", lambda: available)

        class _Rec:
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

    def test_unavailable_returns_none(self, monkeypatch):
        mod = self._patch_voice(monkeypatch, available=(False, "Install voice extra: uv sync --extra voice"))
        assert mod.record_voice_input(_FakeLive(), _console(), _KeySequence(["x"])) is None

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
