"""Tests for the secret-redaction layer (redaction.py)."""

import logging

import pytest

from yeaboi import redaction
from yeaboi.redaction import REDACTED, RedactingFormatter, log_safe, redact


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


class TestLogSafe:
    """`log_safe` is about where a log line may *end*, not what it may say."""

    def test_carriage_return_and_newline_become_spaces(self):
        # The whole attack: close the current record, write a convincing one.
        forged = "bob\r\n2026-01-01 12:00:00 ERROR admin login from 10.0.0.1"
        out = log_safe(forged)
        assert "\n" not in out
        assert "\r" not in out
        assert "ERROR admin login" in out  # kept, but demoted to the same line

    def test_tabs_and_control_characters_dropped(self):
        assert log_safe("a\tb") == "a b"
        assert log_safe("a\x00\x1b[31mb\x7f") == "a[31mb"

    def test_non_string_values_coerced(self):
        assert log_safe(5) == "5"
        assert log_safe(None) == "None"
        assert log_safe(["a", "b"]) == "['a', 'b']"

    def test_long_values_truncated_to_the_limit(self):
        out = log_safe("x" * 5000)
        assert len(out) == redaction.LOG_VALUE_LIMIT
        assert out.endswith("…")

    def test_custom_limit_honoured(self):
        assert log_safe("abcdefghij", limit=5) == "abcd…"

    def test_idempotent(self):
        value = "bob\r\nfoo\x00" + "y" * 500
        assert log_safe(log_safe(value)) == log_safe(value)

    def test_composes_with_redact(self):
        # Both layers apply to real log output: log_safe at the call site,
        # redact in the formatter. Neither may undo the other.
        line = redact(log_safe("key=sk-ant-api03-abcdefghijklmnop\r\nfake"))
        assert "sk-ant" not in line
        assert REDACTED in line
        assert "\n" not in line

    def test_clean_values_pass_through_unchanged(self):
        assert log_safe("YEA-123") == "YEA-123"
        assert log_safe("Ada Lovelace") == "Ada Lovelace"


class TestUrlCredentials:
    """pip and uv echo the package-index URL, and a corporate mirror routinely
    carries a token in it — which the voice installer then logs."""

    def test_index_credentials_are_stripped_but_the_host_survives(self):
        from yeaboi.redaction import redact

        out = redact("Looking in indexes: https://svc:AKCp8xyzSECRETVALUE@nexus.corp/repository/pypi/simple")
        assert "AKCp8xyzSECRETVALUE" not in out
        assert "svc:" not in out
        assert "nexus.corp/repository/pypi/simple" in out

    def test_a_credential_free_url_is_untouched(self):
        from yeaboi.redaction import redact

        assert redact("https://pypi.org/simple") == "https://pypi.org/simple"

    def test_a_bare_port_is_not_mistaken_for_a_password(self):
        from yeaboi.redaction import redact

        assert redact("connecting to https://nexus.corp:8443/simple") == "connecting to https://nexus.corp:8443/simple"

    def test_it_is_idempotent(self):
        from yeaboi.redaction import redact

        once = redact("https://u:pa55word@host/x")
        assert redact(once) == once
