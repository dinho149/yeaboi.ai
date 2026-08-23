"""The dictation routes — status, the offer, the install stream, transcription.

Socketless, over ``AppServer.handle()``. Nothing here installs anything or
imports faster-whisper: the installer is driven through fakes, and the subject
is the wire plus the one decision this surface makes for itself — that the
microphone belongs to the window, so only the transcription half is required.
"""

from __future__ import annotations

import base64
import json

import pytest

from yeaboi import config, voice
from yeaboi.app import routes_voice
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No .env writes, no sticky verdict from the host running the suite."""
    from yeaboi import voice_install

    monkeypatch.setattr(config, "set_config_value", lambda _k, _v: None)
    monkeypatch.setattr(voice_install, "unsupported_reason", lambda: "")
    monkeypatch.setattr(voice_install, "model_is_cached", lambda _size: True)
    monkeypatch.delenv("VOICE_DEVICE", raising=False)
    return monkeypatch


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, headers: dict | None = None):
    head = {"Authorization": f"Bearer {TOKEN}", **(headers or {})}
    body_bytes = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, head, body_bytes))


def body(response) -> dict:
    assert response.code == 200, response.body
    return json.loads(response.body)


def lines(response) -> list[dict]:
    assert response.stream is not None
    return [json.loads(line) for line in response.stream]


def installed(monkeypatch, *, ready: bool) -> None:
    """Fake whether ``faster_whisper`` is importable, without importing it."""
    monkeypatch.setattr(voice, "_installed", lambda name: ready if name == "faster_whisper" else True)


class TestTranscriptionState:
    """The desktop asks a smaller question than the terminal, on purpose."""

    def test_the_speech_engine_alone_is_enough(self, monkeypatch):
        # sounddevice absent, faster_whisper present: the terminal would refuse
        # this machine, the window records its own audio and does not care.
        monkeypatch.setattr(voice, "_installed", lambda name: name == "faster_whisper")
        assert voice.can_transcribe() == (True, "")
        assert voice.transcription_state() == "ready"
        assert voice.is_voice_available()[0] is False

    def test_a_missing_speech_engine_is_installable(self, monkeypatch):
        installed(monkeypatch, ready=False)
        monkeypatch.setattr(config, "is_voice_install_offer_enabled", lambda: True)
        assert voice.transcription_state() == "installable"

    def test_a_declined_offer_is_not_an_install_prompt(self, monkeypatch):
        installed(monkeypatch, ready=False)
        monkeypatch.setattr(config, "is_voice_install_offer_enabled", lambda: False)
        assert voice.transcription_state() == "declined"

    def test_a_host_with_no_wheel_is_unsupported(self, monkeypatch):
        from yeaboi import voice_install

        installed(monkeypatch, ready=False)
        monkeypatch.setattr(voice_install, "unsupported_reason", lambda: "armv7 CPUs have no speech-engine wheel")
        assert voice.transcription_state() == "unsupported"

    def test_the_install_set_is_the_transcription_half(self):
        assert voice.TRANSCRIBE_PACKAGES == ("faster-whisper",)
        from yeaboi.voice_install import VOICE_PACKAGES

        assert set(voice.TRANSCRIBE_PACKAGES) < set(VOICE_PACKAGES)


class TestStatus:
    def test_a_ready_machine_says_so_and_names_the_model(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        payload = body(request(app, "GET", "/api/voice"))
        assert payload["state"] == "ready"
        assert payload["model"] in payload["detail"]
        assert payload["model_cached"] is True
        assert payload["install"]["available"] is False
        assert payload["max_bytes"] == routes_voice.MAX_AUDIO_BYTES

    def test_an_installable_machine_carries_the_offer(self, app, monkeypatch):
        installed(monkeypatch, ready=False)
        monkeypatch.setattr(config, "is_voice_install_offer_enabled", lambda: True)
        payload = body(request(app, "GET", "/api/voice"))
        assert payload["state"] == "installable"
        assert payload["install"]["size_mb"] > 0
        assert payload["install"]["command"]

    def test_the_device_preference_travels_unresolved(self, app, monkeypatch):
        # PortAudio is not consulted here — the window resolves this name
        # against its own device list, and an index would mean nothing to it.
        installed(monkeypatch, ready=True)
        monkeypatch.setenv("VOICE_DEVICE", "Shure MV7")
        assert body(request(app, "GET", "/api/voice"))["device"] == "Shure MV7"


class TestOffer:
    def test_declining_is_the_same_persisted_no_the_terminal_writes(self, app, monkeypatch):
        written: list = []
        monkeypatch.setattr(config, "set_voice_install_offer", lambda enabled: written.append(enabled))
        assert body(request(app, "POST", "/api/voice/offer", {"enabled": False}))["enabled"] is False
        assert written == [False]

    def test_the_offer_can_be_restored(self, app, monkeypatch):
        written: list = []
        monkeypatch.setattr(config, "set_voice_install_offer", lambda enabled: written.append(enabled))
        assert body(request(app, "POST", "/api/voice/offer", {"enabled": True}))["enabled"] is True
        assert written == [True]


class TestTranscribe:
    def test_a_recording_comes_back_as_text(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        seen: list[bytes] = []
        monkeypatch.setattr(voice, "transcribe_media", lambda data: seen.append(data) or "ship the thing")
        payload = {"audio": base64.b64encode(b"opus-bytes").decode(), "mime": "audio/webm"}
        assert body(request(app, "POST", "/api/voice/transcribe", payload))["text"] == "ship the thing"
        assert seen == [b"opus-bytes"]

    def test_a_machine_that_cannot_transcribe_says_so_rather_than_failing_oddly(self, app, monkeypatch):
        installed(monkeypatch, ready=False)
        response = request(app, "POST", "/api/voice/transcribe", {"audio": base64.b64encode(b"x").decode()})
        assert response.code == 409
        assert b"Install the speech engine" in response.body

    def test_an_empty_recording_is_refused(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        assert request(app, "POST", "/api/voice/transcribe", {"audio": ""}).code == 400

    def test_something_that_is_not_base64_is_a_400_not_a_500(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        assert request(app, "POST", "/api/voice/transcribe", {"audio": "not base64!!"}).code == 400

    def test_an_over_long_recording_is_413(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        oversized = base64.b64encode(b"x" * (routes_voice.MAX_AUDIO_BYTES + 1)).decode()
        response = request(app, "POST", "/api/voice/transcribe", {"audio": oversized})
        assert response.code == 413

    def test_a_body_the_server_dropped_reads_as_too_long_not_as_silence(self, app, monkeypatch):
        # The server refuses to read a body past its cap, so the handler sees an
        # empty one. Reporting that as "no speech" would blame the microphone.
        installed(monkeypatch, ready=True)
        response = app.handle(
            parse_request(
                "POST",
                "/api/voice/transcribe",
                {"Authorization": f"Bearer {TOKEN}", "Content-Length": "9000000"},
                b"",
            )
        )
        assert response.code == 413
        assert b"too long" in response.body

    def test_a_transcription_failure_is_reported_not_raised(self, app, monkeypatch):
        installed(monkeypatch, ready=True)

        def boom(_data):
            raise RuntimeError("ctranslate2 said no")

        monkeypatch.setattr(voice, "transcribe_media", boom)
        response = request(app, "POST", "/api/voice/transcribe", {"audio": base64.b64encode(b"x").decode()})
        assert response.code == 500
        assert b"RuntimeError" in response.body


class TestInstall:
    @pytest.fixture
    def fake_installer(self, monkeypatch):
        """Both children replaced: nothing is downloaded and nothing is spawned."""
        from yeaboi import voice_install

        monkeypatch.setattr(config, "mark_voice_extra_installed", lambda: None)
        monkeypatch.setattr(
            voice_install,
            "install_packages",
            lambda on_line, cancel, plan=None: (on_line("resolving faster-whisper"), (True, ""))[1],
        )
        monkeypatch.setattr(
            voice_install,
            "download_model",
            lambda size, on_progress, cancel: (on_progress("70/145 MB", 0.5), (True, ""))[1],
        )
        monkeypatch.setattr(voice_install, "warm_model", lambda size: (True, ""))
        return monkeypatch

    def test_the_stream_walks_the_same_four_stages_the_terminal_animates(self, app, monkeypatch, fake_installer):
        installed(monkeypatch, ready=False)
        events = lines(request(app, "POST", "/api/voice/install"))
        assert events[0]["type"] == "op"
        stages = [e["stage"] for e in events if e["type"] == "stage"]
        assert stages == ["install", "download", "download", "load"]
        assert events[-1] == {"type": "done", "warning": ""}

    def test_the_download_reports_a_real_fraction(self, app, monkeypatch, fake_installer):
        installed(monkeypatch, ready=False)
        events = lines(request(app, "POST", "/api/voice/install"))
        progress = [e for e in events if e.get("stage") == "download" and e["fraction"] is not None]
        assert progress[0]["fraction"] == 0.5
        assert progress[0]["detail"] == "70/145 MB"

    def test_a_failed_model_download_is_a_warning_not_a_failure(self, app, monkeypatch, fake_installer):
        # The packages are in; the weights arrive lazily on the first dictation.
        from yeaboi import voice_install

        installed(monkeypatch, ready=False)
        monkeypatch.setattr(voice_install, "download_model", lambda *a, **k: (False, "Can't reach huggingface.co"))
        events = lines(request(app, "POST", "/api/voice/install"))
        assert events[-1] == {"type": "done", "warning": "Can't reach huggingface.co"}

    def test_a_failed_package_install_ends_the_stream_with_an_error(self, app, monkeypatch, fake_installer):
        from yeaboi import voice_install

        installed(monkeypatch, ready=False)
        monkeypatch.setattr(voice_install, "install_packages", lambda *a, **k: (False, "Out of disk"))
        events = lines(request(app, "POST", "/api/voice/install"))
        assert events[-1] == {"type": "error", "message": "Out of disk"}

    def test_the_op_is_removed_when_the_stream_ends(self, app, monkeypatch, fake_installer):
        installed(monkeypatch, ready=False)
        lines(request(app, "POST", "/api/voice/install"))
        assert len(app.ops) == 0

    def test_installing_over_a_working_install_is_refused(self, app, monkeypatch):
        installed(monkeypatch, ready=True)
        response = request(app, "POST", "/api/voice/install")
        assert response.code == 409
        assert b"already set up" in response.body

    def test_a_host_no_wheel_exists_for_never_spawns_anything(self, app, monkeypatch):
        from yeaboi import voice_install

        installed(monkeypatch, ready=False)
        monkeypatch.setattr(voice_install, "platform_support", lambda: (False, "musl libc"))
        response = request(app, "POST", "/api/voice/install")
        assert response.code == 409
        assert b"musl libc" in response.body
