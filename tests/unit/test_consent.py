"""Tests for the filesystem-sandbox consent surface (ui/shared/_consent.py)
and the post-turn consent drain loop (ui/session/phases/_phases.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi import fs_policy
from yeaboi.fs_policy import ConsentRequest
from yeaboi.ui.shared import _consent


class _FakeConsole:
    size = (100, 36)


class _FakeLive:
    """Captures the last renderable so tests can assert on the painted popup."""

    def __init__(self):
        self.last = None

    def update(self, renderable):
        self.last = renderable


@pytest.fixture(autouse=True)
def _reset_fs_policy_state():
    """Consent tests mutate fs_policy process state — always restore it."""
    yield
    fs_policy.clear_session_grants()
    fs_policy.set_interactive(False)
    fs_policy.pop_pending_denials()


def _req(path: str = "/definitely/outside/file.txt", mode: str = "read", context: str = "Test feature"):
    return ConsentRequest(Path(path), mode, context)


def _scripted_read_key(keys: list[str]):
    seq = iter(keys)

    def read_key(timeout=None):
        return next(seq)

    return read_key


def _run_popup(keys: list[str], req=None, live=None):
    return _consent._fs_consent_popup(
        _FakeConsole(), live or _FakeLive(), _scripted_read_key(keys), 0.001, True, req or _req()
    )


class TestFsConsentPopup:
    def test_enter_selects_allow_once(self):
        assert _run_popup(["enter"]) == "allow_once"

    def test_space_selects_too(self):
        assert _run_popup([" "]) == "allow_once"

    def test_right_then_enter_selects_allow_always(self):
        assert _run_popup(["right", "enter"]) == "allow_always"

    def test_two_rights_then_enter_selects_deny(self):
        assert _run_popup(["right", "right", "enter"]) == "deny"

    def test_right_clamps_at_deny(self):
        assert _run_popup(["right", "right", "right", "enter"]) == "deny"

    def test_left_clamps_at_allow_once(self):
        assert _run_popup(["left", "enter"]) == "allow_once"

    def test_esc_is_deny(self):
        assert _run_popup(["esc"]) == "deny"

    def test_q_is_deny(self):
        assert _run_popup(["q"]) == "deny"

    def test_tab_cycles_forward_and_wraps(self):
        assert _run_popup(["tab", "enter"]) == "allow_always"
        assert _run_popup(["tab", "tab", "enter"]) == "deny"
        assert _run_popup(["tab", "tab", "tab", "enter"]) == "allow_once"

    def test_idle_tick_is_ignored(self):
        assert _run_popup(["", "enter"]) == "allow_once"

    def test_renders_path_mode_context_and_sandbox_hint(self):
        live = _FakeLive()
        _run_popup(["enter"], req=_req("/repos/team", "read", "Roadmap intake — local file"), live=live)
        assert isinstance(live.last, Panel)
        console = Console(width=100, force_terminal=False)
        with console.capture() as cap:
            console.print(live.last)
        out = cap.get()
        assert "/repos/team" in out
        assert "read from" in out
        assert "Roadmap intake" in out
        assert "yeaboi only accesses ~/.yeaboi unless you allow a path" in out
        assert "Allow once" in out
        assert "Always allow" in out
        assert "Deny" in out

    def test_write_mode_renders_write_verb(self):
        live = _FakeLive()
        _run_popup(["enter"], req=_req("/repos/out", "write", "Export"), live=live)
        console = Console(width=100, force_terminal=False)
        with console.capture() as cap:
            console.print(live.last)
        assert "write to" in cap.get()


class TestApplyConsent:
    def test_allow_once_grants_session_only(self, monkeypatch):
        grants, persisted = [], []
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", grants.append)
        monkeypatch.setattr("yeaboi.config.add_allowed_path", persisted.append)
        assert _consent._apply_consent("allow_once", _req()) is True
        assert grants == [Path("/definitely/outside/file.txt")]
        assert persisted == []

    def test_allow_always_persists_to_whitelist(self, monkeypatch):
        grants, persisted = [], []
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", grants.append)
        monkeypatch.setattr("yeaboi.config.add_allowed_path", persisted.append)
        assert _consent._apply_consent("allow_always", _req()) is True
        assert persisted == ["/definitely/outside/file.txt"]
        assert grants == []

    def test_deny_does_nothing(self, monkeypatch):
        grants, persisted = [], []
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", grants.append)
        monkeypatch.setattr("yeaboi.config.add_allowed_path", persisted.append)
        assert _consent._apply_consent("deny", _req()) is False
        assert grants == []
        assert persisted == []


class TestPreflightPathConsent:
    def _preflight(self, path, **kwargs):
        return _consent._preflight_path_consent(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", 0.001, True, path, **kwargs
        )

    def test_allowed_path_skips_popup(self, monkeypatch):
        monkeypatch.setattr(
            _consent,
            "_fs_consent_popup",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("popup must not open")),
        )
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": True)
        assert self._preflight("/anywhere") is True

    def test_denied_path_popup_deny_returns_false(self, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "deny")
        assert self._preflight("/definitely/outside") is False

    def test_denied_path_popup_allow_once_grants_and_returns_true(self, monkeypatch):
        grants = []
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", grants.append)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "allow_once")
        assert self._preflight("/definitely/outside", context="Test") is True
        assert grants and str(grants[0]).endswith("outside")

    def test_popup_receives_resolved_request(self, monkeypatch):
        seen = []
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)

        def _popup(console, live, read_key, frame_time, supports_timeout, req):
            seen.append(req)
            return "deny"

        monkeypatch.setattr(_consent, "_fs_consent_popup", _popup)
        self._preflight("~/nonexistent-sandbox-test", mode="read", context="Ctx")
        assert len(seen) == 1
        assert seen[0].path.is_absolute()  # expanduser + resolve applied
        assert seen[0].context == "Ctx"


class TestPreflightPathChoice:
    """The tri-state variant. A caller that SAVES a path needs to know whether
    the grant outlives this process — an allow-once grant behind a persisted
    setting reviews nothing on every scheduled run afterwards."""

    def _choice(self, path, **kwargs):
        return _consent._preflight_path_choice(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", 0.001, True, path, **kwargs
        )

    def test_already_allowed_is_distinguishable_from_a_fresh_grant(self, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": True)
        assert self._choice("/anywhere") == "already_allowed"

    def test_allow_once_is_reported_as_such(self, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", lambda p: None)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "allow_once")
        assert self._choice("/definitely/outside") == "allow_once"

    def test_allow_always_is_reported_as_such(self, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr("yeaboi.config.add_allowed_path", lambda p: None)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "allow_always")
        assert self._choice("/definitely/outside") == "allow_always"

    def test_deny(self, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "deny")
        assert self._choice("/definitely/outside") == "deny"

    def test_the_boolean_wrapper_is_unchanged(self, monkeypatch):
        """Every existing call site keeps its behaviour."""
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda path, mode="read": False)
        monkeypatch.setattr("yeaboi.fs_policy.grant_session", lambda p: None)
        for popup, expected in (("allow_once", True), ("allow_always", True), ("deny", False)):
            monkeypatch.setattr("yeaboi.config.add_allowed_path", lambda p: None)
            monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, _p=popup, **k: _p)
            got = _consent._preflight_path_consent(
                _FakeConsole(), _FakeLive(), lambda timeout=None: "", 0.001, True, "/definitely/outside"
            )
            assert got is expected


class TestDrainSandboxConsents:
    """Post-turn loop: queued denial + consent stub → grant + synthetic retry turn."""

    def _queue_denial(self, path: str = "/definitely/not/allowed/repo") -> None:
        fs_policy.set_interactive(True)
        with pytest.raises(PermissionError):
            fs_policy.resolve_and_check(path, mode="read", context="tool: load_project_context")

    def test_allow_always_persists_and_injects_retry_turn(self, monkeypatch):
        from yeaboi.ui.session.phases import _phases

        self._queue_denial()
        persisted, turns, notes = [], [], []
        monkeypatch.setattr("yeaboi.config.add_allowed_path", persisted.append)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "allow_always")

        _phases._drain_sandbox_consents(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", turns.append, notes.append
        )

        assert len(persisted) == 1 and persisted[0].endswith("repo")
        assert len(turns) == 1
        assert "has been granted — please retry" in turns[0]
        assert notes == turns  # the synthetic message is shown in the transcript

    def test_allow_once_grants_session_and_retries(self, monkeypatch):
        from yeaboi.ui.session.phases import _phases

        self._queue_denial()
        turns = []
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "allow_once")

        _phases._drain_sandbox_consents(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", turns.append, lambda t: None
        )

        assert len(turns) == 1
        assert fs_policy.is_allowed("/definitely/not/allowed/repo/sub.txt", mode="read")

    def test_deny_does_not_retry(self, monkeypatch):
        from yeaboi.ui.session.phases import _phases

        self._queue_denial()
        persisted, turns = [], []
        monkeypatch.setattr("yeaboi.config.add_allowed_path", persisted.append)
        monkeypatch.setattr(_consent, "_fs_consent_popup", lambda *a, **k: "deny")

        _phases._drain_sandbox_consents(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", turns.append, lambda t: None
        )

        assert persisted == []
        assert turns == []

    def test_empty_queue_is_a_noop(self, monkeypatch):
        from yeaboi.ui.session.phases import _phases

        monkeypatch.setattr(
            _consent,
            "_fs_consent_popup",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("popup must not open")),
        )
        turns = []
        _phases._drain_sandbox_consents(
            _FakeConsole(), _FakeLive(), lambda timeout=None: "", turns.append, lambda t: None
        )
        assert turns == []
