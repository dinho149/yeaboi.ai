"""Tests for the central logging setup module (logging_setup.py)."""

import logging
import os
from logging.handlers import RotatingFileHandler

import pytest

from yeaboi import logging_setup
from yeaboi.logging_setup import (
    BACKUP_COUNT,
    DATE_FORMAT,
    LOG_FORMAT,
    MAX_BYTES,
    apply_level,
    attach_mode_handler,
    attach_session_log,
    configure_logging,
    detach,
    detach_session_log,
    mode_log,
)


@pytest.fixture(autouse=True)
def _isolated_logging(monkeypatch, tmp_path):
    """Point all log paths at tmp_path and guarantee no handler leaks."""
    monkeypatch.setattr("yeaboi.paths.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("yeaboi.paths.PLANNING_LOGS_DIR", tmp_path / "logs" / "planning")
    monkeypatch.setattr("yeaboi.paths.get_tui_log_path", lambda: tmp_path / "logs" / "tui" / "yeaboi.log")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    yield
    for key in list(logging_setup._handlers):
        detach(key)


def _app_handlers():
    return [h for h in logging.getLogger("yeaboi").handlers if h in logging_setup._handlers.values()]


class TestConfigureLogging:
    def test_attaches_rotating_tui_handler(self, tmp_path):
        configure_logging()
        handler = logging_setup._handlers["tui"]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == MAX_BYTES == 2 * 1024 * 1024
        assert handler.backupCount == BACKUP_COUNT == 3
        assert handler.formatter._fmt == LOG_FORMAT
        assert handler.formatter.datefmt == DATE_FORMAT
        assert handler.baseFilename.endswith("yeaboi.log")
        assert (tmp_path / "logs" / "tui").is_dir()

    def test_idempotent(self):
        configure_logging()
        configure_logging()
        assert len(_app_handlers()) == 1

    def test_default_level_is_warning(self):
        configure_logging()
        assert logging_setup._handlers["tui"].level == logging.WARNING

    def test_level_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        configure_logging()
        assert logging_setup._handlers["tui"].level == logging.DEBUG


class TestModeHandler:
    def test_creates_mode_log_file(self, tmp_path):
        attach_mode_handler("retro")
        handler = logging_setup._handlers["retro"]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.baseFilename == str(tmp_path / "logs" / "retro" / "retro.log")

    def test_double_attach_is_noop(self):
        attach_mode_handler("standup")
        attach_mode_handler("standup")
        assert len(_app_handlers()) == 1

    def test_detach_removes_and_closes(self):
        attach_mode_handler("reporting")
        handler = logging_setup._handlers["reporting"]
        detach("reporting")
        assert "reporting" not in logging_setup._handlers
        assert handler not in logging.getLogger("yeaboi").handlers

    def test_detach_unknown_key_is_noop(self):
        detach("never-attached")  # must not raise

    def test_records_land_in_mode_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        attach_mode_handler("performance")
        logging.getLogger("yeaboi.test").info("hello from performance")
        detach("performance")
        content = (tmp_path / "logs" / "performance" / "performance.log").read_text()
        assert "hello from performance" in content
        assert "INFO" in content


class TestModeLogContextManager:
    def test_detaches_on_normal_exit(self):
        with mode_log("retro"):
            assert "retro" in logging_setup._handlers
        assert "retro" not in logging_setup._handlers

    def test_detaches_on_exception(self):
        with pytest.raises(RuntimeError), mode_log("retro"):
            raise RuntimeError("boom")
        assert "retro" not in logging_setup._handlers


class TestSessionLog:
    def test_creates_planning_session_file(self, tmp_path):
        attach_session_log("new-abc123-2026-07-18")
        handler = logging_setup._handlers["session"]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.baseFilename == str(tmp_path / "logs" / "planning" / "new-abc123-2026-07-18.log")

    def test_new_session_replaces_previous(self):
        attach_session_log("session-one")
        first = logging_setup._handlers["session"]
        attach_session_log("session-two")
        second = logging_setup._handlers["session"]
        assert first is not second
        assert first not in logging.getLogger("yeaboi").handlers
        assert second.baseFilename.endswith("session-two.log")
        assert len(_app_handlers()) == 1

    def test_detach_session_log(self):
        attach_session_log("session-x")
        detach_session_log()
        assert "session" not in logging_setup._handlers


class TestApplyLevel:
    def test_updates_logger_and_all_handlers(self):
        configure_logging()
        attach_mode_handler("standup")
        apply_level("DEBUG")
        assert logging.getLogger("yeaboi").level == logging.DEBUG
        assert all(h.level == logging.DEBUG for h in logging_setup._handlers.values())
        apply_level("ERROR")
        assert logging.getLogger("yeaboi").level == logging.ERROR
        assert all(h.level == logging.ERROR for h in logging_setup._handlers.values())

    def test_invalid_level_falls_back_to_warning(self):
        configure_logging()
        apply_level("NOT-A-LEVEL")
        assert logging.getLogger("yeaboi").level == logging.WARNING

    def test_lowercase_accepted(self):
        configure_logging()
        apply_level("info")
        assert logging.getLogger("yeaboi").level == logging.INFO


class TestRedactionAndPermissions:
    """The security layer: every handler redacts secrets and hardens file perms."""

    def _log_via_handler(self, tmp_path, message, *, exc=None):
        configure_logging()
        apply_level("INFO")
        log = logging.getLogger("yeaboi.test_security")
        if exc is not None:
            try:
                raise exc
            except Exception:
                log.exception(message)
        else:
            log.info(message)
        detach("tui")
        return (tmp_path / "logs" / "tui" / "yeaboi.log").read_text()

    def test_secret_in_message_redacted_in_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop")
        content = self._log_via_handler(tmp_path, "auth with sk-ant-api03-abcdefghijklmnop failed")
        assert "sk-ant-api03" not in content
        assert "[REDACTED]" in content

    def test_secret_in_exception_redacted_in_file(self, tmp_path):
        content = self._log_via_handler(
            tmp_path, "call failed", exc=RuntimeError("401 Bearer abcdefghijklmnopqrstuvwx")
        )
        assert "abcdefghijklmnopqrstuvwx" not in content
        assert "[REDACTED]" in content
        assert "RuntimeError" in content

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_log_file_and_dir_permissions(self, tmp_path):
        self._log_via_handler(tmp_path, "hello")
        log_file = tmp_path / "logs" / "tui" / "yeaboi.log"
        assert (log_file.stat().st_mode & 0o777) == 0o600
        assert (log_file.parent.stat().st_mode & 0o777) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_rollover_keeps_restricted_permissions(self, tmp_path):
        path = tmp_path / "logs" / "roll" / "roll.log"
        path.parent.mkdir(parents=True)
        handler = logging_setup._SecureRotatingFileHandler(path, maxBytes=64, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log = logging.getLogger("yeaboi.test_rollover")
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        try:
            for _ in range(20):
                log.info("x" * 32)
        finally:
            log.removeHandler(handler)
            handler.close()
        assert (path.stat().st_mode & 0o777) == 0o600  # the post-rollover base file
