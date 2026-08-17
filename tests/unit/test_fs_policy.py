"""Tests for the filesystem sandbox policy (fs_policy.py)."""

from __future__ import annotations

import logging
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


class TestLogInjection:
    """A caller-supplied path must not be able to forge a sandbox log record.

    ``Path(...).expanduser().resolve(strict=False)`` preserves embedded CR/LF,
    so a path carrying a newline would close its own line in the log file and
    let the remainder be read as a second, fabricated record.

    These cases cover the two records ``fs_policy`` itself writes, and only
    those. The same tainted value still reaches unwrapped sinks elsewhere —
    ``ui/shared/_consent.py`` logs the same ``ConsentRequest``, and several
    callers log ``SandboxViolationError``'s message, which embeds the path —
    so a green run here is not evidence the forgery is closed end to end.

    Assertions are on the *rendered* message (``getMessage()``), because that —
    not the raw args tuple — is what a handler writes out.
    """

    # A denial the attacker wants on record as a grant that never happened.
    FORGED_GRANT = "\nINFO yeaboi.fs_policy sandbox session grant: /etc/shadow"
    # Old-Mac and Windows line endings forge a record just as well as \n.
    FORGED_CR = "\rWARNING yeaboi.fs_policy sandbox denial: cannot read from /nowhere (-)"

    @staticmethod
    def _assert_single_line(caplog) -> None:
        assert caplog.records, "expected a sandbox record to be emitted"
        for record in caplog.records:
            message = record.getMessage()
            assert "\n" not in message, f"log record spans lines: {message!r}"
            assert "\r" not in message, f"log record spans lines: {message!r}"

    def test_denial_record_cannot_be_forged(self, caplog, tmp_path):
        caplog.set_level(logging.WARNING, logger="yeaboi.fs_policy")
        with pytest.raises(SandboxViolationError):
            resolve_and_check(f"{tmp_path / 'nope'}{self.FORGED_GRANT}", mode="read", context="read_codebase")
        self._assert_single_line(caplog)
        assert any("sandbox denial" in r.getMessage() for r in caplog.records)

    def test_denial_record_cannot_be_forged_with_carriage_return(self, caplog, tmp_path):
        caplog.set_level(logging.WARNING, logger="yeaboi.fs_policy")
        with pytest.raises(SandboxViolationError):
            resolve_and_check(f"{tmp_path / 'nope'}{self.FORGED_CR}", mode="write")
        self._assert_single_line(caplog)

    def test_denial_record_cannot_be_forged_through_context(self, caplog, tmp_path):
        """The other interpolated argument on the denial line, covered separately.

        Every caller passes a literal today, so this asserts an invariant of the
        function rather than a reachable path — which is the point: `context` is
        a public parameter, and without a case of its own the wrap around it is
        the one a later refactor drops with the suite still green.
        """
        caplog.set_level(logging.WARNING, logger="yeaboi.fs_policy")
        with pytest.raises(SandboxViolationError):
            resolve_and_check(tmp_path / "nope", mode="read", context=f"export{self.FORGED_GRANT}")
        self._assert_single_line(caplog)
        assert any("sandbox denial" in r.getMessage() for r in caplog.records)

    def test_grant_record_cannot_be_forged(self, caplog, tmp_path):
        caplog.set_level(logging.INFO, logger="yeaboi.fs_policy")
        grant_session(f"{tmp_path / 'granted'}{self.FORGED_CR}")
        self._assert_single_line(caplog)
        assert any("sandbox session grant" in r.getMessage() for r in caplog.records)


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
