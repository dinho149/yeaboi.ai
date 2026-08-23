"""Tests for the filesystem sandbox policy (fs_policy.py)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yeaboi import fs_policy, paths
from yeaboi.fs_policy import (
    BUILTIN_ALLOWED,
    SandboxViolationError,
    clear_session_grants,
    grant_session,
    is_allowed,
    pop_pending_denials,
    resolve_and_check,
    set_interactive,
)


@pytest.fixture(autouse=True)
def _isolated_policy(monkeypatch, tmp_path):
    """Root the sandbox at tmp_path/home/.yeaboi and reset all session state."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))  # expanduser() reads $HOME, not Path.home
    monkeypatch.setattr(paths, "ROOT_DIR", home / ".yeaboi")
    monkeypatch.setattr(paths, "DEFAULT_ROOT_DIR", home / ".yeaboi")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
    clear_session_grants()
    set_interactive(False)
    pop_pending_denials()
    yield
    clear_session_grants()
    set_interactive(False)
    pop_pending_denials()


class TestRootContainment:
    def test_inside_root_allowed(self, tmp_path):
        target = tmp_path / "home" / ".yeaboi" / "exports" / "x.md"
        assert is_allowed(target, mode="write")
        assert resolve_and_check(target, mode="write") == target

    def test_outside_root_denied(self, tmp_path):
        with pytest.raises(SandboxViolationError):
            resolve_and_check(tmp_path / "elsewhere" / "x.txt")

    def test_relocated_home_followed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "ROOT_DIR", tmp_path / "relocated")
        assert is_allowed(tmp_path / "relocated" / "data" / "db.sqlite", mode="write")

    def test_pinned_default_root_still_allowed_when_relocated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "ROOT_DIR", tmp_path / "relocated")
        # ~/.yeaboi/.env stays readable/writable — it bootstraps YEABOI_HOME.
        assert is_allowed(tmp_path / "home" / ".yeaboi" / ".env", mode="write")

    def test_sibling_prefix_is_not_containment(self, monkeypatch, tmp_path):
        """/tmp/repo allowed must NOT authorize /tmp/repo-secret."""
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(tmp_path / "repo"))
        assert is_allowed(tmp_path / "repo" / "file.py")
        assert not is_allowed(tmp_path / "repo-secret" / "file.py")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
    def test_symlink_escape_denied(self, monkeypatch, tmp_path):
        """A symlink inside an allowed dir pointing outside resolves outside → denied."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        secret = tmp_path / "secret"
        secret.mkdir()
        (allowed / "link").symlink_to(secret)
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(allowed))
        assert not is_allowed(allowed / "link" / "creds.txt")
        with pytest.raises(SandboxViolationError):
            resolve_and_check(allowed / "link" / "creds.txt")


class TestBuiltinAllowlist:
    def test_cwd_env_read_only(self, tmp_path):
        assert is_allowed(tmp_path / ".env", mode="read")
        assert not is_allowed(tmp_path / ".env", mode="write")

    def test_cwd_scrum_md_and_docs_readable(self, tmp_path):
        assert is_allowed(tmp_path / "SCRUM.md", mode="read")
        assert is_allowed(tmp_path / "scrum-docs" / "notes.md", mode="read")
        assert not is_allowed(tmp_path / "other.md", mode="read")

    def test_aws_config_read_only(self, tmp_path):
        aws = tmp_path / "home" / ".aws" / "config"
        assert is_allowed(aws, mode="read")
        assert not is_allowed(aws, mode="write")
        # And only the config file, not the credentials next to it.
        assert not is_allowed(tmp_path / "home" / ".aws" / "credentials", mode="read")

    def test_launchagents_and_legacy_root_writable(self, tmp_path):
        home = tmp_path / "home"
        assert is_allowed(home / "Library" / "LaunchAgents" / "ai.yeaboi.standup.plist", mode="write")
        assert is_allowed(home / ".scrum-agent" / "sessions.db", mode="write")

    def test_every_builtin_documents_a_reason(self):
        assert all(rule.reason for rule in BUILTIN_ALLOWED)


class TestUserWhitelist:
    def test_whitelisted_dir_allows_read_and_write(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(tmp_path / "proj"))
        assert is_allowed(tmp_path / "proj" / "src" / "a.py", mode="read")
        assert is_allowed(tmp_path / "proj" / "out.html", mode="write")

    def test_multiple_entries_and_tilde_expansion(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", f"{tmp_path / 'a'},~/whitelisted")
        assert is_allowed(tmp_path / "a" / "x")
        assert is_allowed(tmp_path / "home" / "whitelisted" / "y")


class TestSessionGrants:
    def test_grant_session_allows(self, tmp_path):
        target = tmp_path / "granted"
        assert not is_allowed(target / "f.txt")
        grant_session(target)
        assert is_allowed(target / "f.txt", mode="read")
        assert is_allowed(target / "f.txt", mode="write")

    def test_clear_session_grants(self, tmp_path):
        grant_session(tmp_path / "granted")
        clear_session_grants()
        assert not is_allowed(tmp_path / "granted" / "f.txt")


class TestConsentQueue:
    def test_headless_denial_does_not_queue(self, tmp_path):
        with pytest.raises(SandboxViolationError):
            resolve_and_check(tmp_path / "nope")
        assert pop_pending_denials() == []

    def test_interactive_denial_queues_and_still_raises(self, tmp_path):
        set_interactive(True)
        with pytest.raises(SandboxViolationError):
            resolve_and_check(tmp_path / "nope" / "f.txt", mode="read", context="read_codebase")
        pending = pop_pending_denials()
        assert len(pending) == 1
        assert pending[0].path == tmp_path / "nope" / "f.txt"
        assert pending[0].mode == "read"
        assert pending[0].context == "read_codebase"
        assert pop_pending_denials() == []  # drained

    def test_duplicate_denials_deduplicated(self, tmp_path):
        set_interactive(True)
        for _ in range(3):
            with pytest.raises(SandboxViolationError):
                resolve_and_check(tmp_path / "nope")
        assert len(pop_pending_denials()) == 1


class TestApplyConsent:
    """What an answer *means* lives here, so every consent surface agrees."""

    def _request(self, tmp_path):
        from yeaboi.fs_policy import ConsentRequest

        return ConsentRequest(tmp_path / "repo", "read", "read_codebase")

    def test_allow_once_grants_for_this_process_only(self, tmp_path):
        req = self._request(tmp_path)
        assert fs_policy.apply_consent("allow_once", req) is True
        assert is_allowed(req.path / "file.py")
        clear_session_grants()
        assert not is_allowed(req.path / "file.py")

    def test_allow_always_writes_the_whitelist(self, tmp_path, monkeypatch):
        written: list[str] = []
        monkeypatch.setattr("yeaboi.config.add_allowed_path", written.append)
        assert fs_policy.apply_consent("allow_always", self._request(tmp_path)) is True
        assert written == [str(tmp_path / "repo")]

    def test_deny_changes_nothing(self, tmp_path):
        req = self._request(tmp_path)
        assert fs_policy.apply_consent("deny", req) is False
        assert not is_allowed(req.path)

    def test_an_unknown_answer_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="unknown consent choice"):
            fs_policy.apply_consent("maybe", self._request(tmp_path))

    def test_the_tui_popup_applies_answers_through_this(self, tmp_path):
        from yeaboi.ui.shared._consent import _apply_consent

        req = self._request(tmp_path)
        assert _apply_consent("allow_once", req) is True
        assert is_allowed(req.path)


class TestRequestConsent:
    """The pre-flight half: a surface that checks before it acts still asks."""

    def test_an_allowed_path_asks_nothing(self, tmp_path):
        target = tmp_path / "home" / ".yeaboi" / "exports"
        set_interactive(True)
        assert fs_policy.request_consent(target) is True
        assert pop_pending_denials() == []

    def test_a_path_outside_queues_a_request_without_raising(self, tmp_path):
        set_interactive(True)
        assert fs_policy.request_consent(tmp_path / "repo", mode="write", context="ship run") is False
        pending = pop_pending_denials()
        assert len(pending) == 1
        assert pending[0].mode == "write"
        assert pending[0].context == "ship run"

    def test_headless_still_refuses_but_queues_nothing(self, tmp_path):
        assert fs_policy.request_consent(tmp_path / "repo") is False
        assert pop_pending_denials() == []


class TestViolationMessage:
    def test_names_every_remedy(self, tmp_path):
        with pytest.raises(SandboxViolationError) as exc_info:
            resolve_and_check(tmp_path / "denied.txt", mode="write", context="export")
        message = str(exc_info.value)
        assert "YEABOI_ALLOWED_PATHS" in message
        assert "--allow-path" in message
        assert "Allowed Paths" in message
        assert "write to" in message
        assert str(tmp_path / "denied.txt") in message

    def test_is_a_permission_error(self, tmp_path):
        with pytest.raises(PermissionError):
            resolve_and_check(tmp_path / "denied.txt")


class TestThreadSafety:
    def test_concurrent_checks_and_grants(self, tmp_path):
        import threading

        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                grant_session(tmp_path / f"dir-{i}")
                for j in range(50):
                    is_allowed(tmp_path / f"dir-{j % 8}" / "f.txt")
                    fs_policy.set_interactive(i % 2 == 0)
            except Exception as e:  # pragma: no cover - only on race
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
