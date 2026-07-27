"""Tests for the secret-redaction layer (redaction.py)."""

import logging

import pytest

from yeaboi import redaction
from yeaboi.redaction import REDACTED, RedactingFormatter, redact


@pytest.fixture(autouse=True)
def _clear_secret_env(monkeypatch):
    """Start every test with no secret env vars set (and a cold regex cache)."""
    for key in redaction.SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    redaction._cache_key = None
    redaction._cache_regex = None


class TestPatternRedaction:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-ant-api03-abcdefghijklmnop",
            "sk-proj-abcdefghijklmnopqrstuv",
            "ghp_abcdefghijklmnopqrstuvwx",
            "github_pat_11ABCDEFG_abcdefghijklmnop",
            "xoxb-1234567890-abcdefghij",
            "AIzaSyA" + "a" * 32,
            "AKIAIOSFODNN7EXAMPLE",
            "ATATT3xFfGF0abcdefghijklmnop",
            "ntn_abcdefghijklmnopqrstuv",
            "secret_" + "a" * 32,
            "hooks.slack.com/services/T000/B000/xyz",
            "Bearer abcdefghijklmnopqrstuvwx",
            "basic dXNlcjpwYXNzd29yZDEyMw==",
        ],
    )
    def test_token_shapes_redacted(self, secret):
        assert secret not in redact(f"request failed: {secret} rejected")

    def test_prose_untouched(self):
        text = "the bearer of good news skipped the basic setup on port 8080"
        assert redact(text) == text

    def test_multiple_secrets_in_one_line(self):
        out = redact("a=ghp_abcdefghijklmnopqrstuvwx b=xoxb-1234567890-abcdefghij")
        assert out == f"a={REDACTED} b={REDACTED}"


class TestValueRedaction:
    def test_env_value_redacted(self, monkeypatch):
        monkeypatch.setenv("JIRA_API_TOKEN", "my-plain-looking-token-value")
        assert redact("auth failed with my-plain-looking-token-value") == f"auth failed with {REDACTED}"

    def test_short_values_not_matched(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "true")
        assert redact("value is true") == "value is true"

    def test_cache_rebuilds_on_env_change(self, monkeypatch):
        assert redact("swordfish-credential") == "swordfish-credential"
        monkeypatch.setenv("NOTION_TOKEN", "swordfish-credential")
        assert redact("swordfish-credential") == REDACTED

    def test_idempotent(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "some-long-secret-value")
        once = redact("key some-long-secret-value here")
        assert redact(once) == once


class TestRedactingFormatter:
    def _format(self, record):
        return RedactingFormatter("%(message)s").format(record)

    def _record(self, msg, *args, exc_info=None):
        return logging.LogRecord("yeaboi.test", logging.ERROR, __file__, 1, msg, args, exc_info)

    def test_message_and_args_redacted(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop")
        out = self._format(self._record("auth: %s", "sk-ant-api03-abcdefghijklmnop"))
        assert "sk-ant" not in out
        assert REDACTED in out

    def test_exception_traceback_redacted(self):
        try:
            raise RuntimeError("401 for Bearer abcdefghijklmnopqrstuvwx")
        except RuntimeError:
            import sys

            record = self._record("call failed", exc_info=sys.exc_info())
        out = self._format(record)
        assert "abcdefghijklmnopqrstuvwx" not in out
        assert REDACTED in out
        assert "RuntimeError" in out  # traceback structure survives

    def test_record_not_mutated(self):
        record = self._record("token ghp_abcdefghijklmnopqrstuvwx")
        self._format(record)
        assert "ghp_" in record.getMessage()  # other handlers see the original
