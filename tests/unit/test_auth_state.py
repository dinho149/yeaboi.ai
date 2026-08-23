"""Tests for yeaboi.auth_state — is the subscription token still any good?

Staleness is observed from two independent places (a live auth failure and a
launch probe) that agree on one in-memory flag, so these cover both routes in and
the two ways it goes back out again.
"""

from __future__ import annotations

import anthropic
import httpx
import pytest

from yeaboi import auth_state


@pytest.fixture(autouse=True)
def _clean_flag():
    auth_state.clear_subscription_stale()
    auth_state.clear_credential_cache()
    yield
    auth_state.clear_subscription_stale()
    auth_state.clear_credential_cache()


@pytest.fixture
def on_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "subscription")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")


def _auth_error() -> anthropic.AuthenticationError:
    return anthropic.AuthenticationError(
        "invalid bearer token",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None,
    )


class TestFlag:
    def test_starts_clear(self):
        assert auth_state.subscription_stale() is False

    def test_mark_and_clear(self):
        auth_state.mark_subscription_stale("AuthenticationError")
        assert auth_state.subscription_stale() is True
        assert auth_state.stale_reason() == "AuthenticationError"
        auth_state.clear_subscription_stale()
        assert auth_state.subscription_stale() is False


class TestNoteAuthFailure:
    def test_flags_under_subscription_auth(self, on_subscription):
        auth_state.note_auth_failure(_auth_error())
        assert auth_state.subscription_stale() is True

    def test_ignores_api_key_auth(self, monkeypatch):
        # A rejected API key is the user's own key — pointing them at the
        # subscription sign-in would send them somewhere that cannot help.
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "api_key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
        auth_state.note_auth_failure(_auth_error())
        assert auth_state.subscription_stale() is False


class TestNodesHook:
    """Every engine's auth error routes through the nodes predicate, which is the
    single place that learns the token stopped working."""

    def test_classifying_an_auth_error_flags_the_token(self, on_subscription):
        from yeaboi.agent.nodes import _is_llm_auth_or_billing_error

        assert _is_llm_auth_or_billing_error(_auth_error()) is True
        assert auth_state.subscription_stale() is True

    def test_a_non_auth_error_changes_nothing(self, on_subscription):
        from yeaboi.agent.nodes import _is_llm_auth_or_billing_error

        assert _is_llm_auth_or_billing_error(ValueError("nope")) is False
        assert auth_state.subscription_stale() is False


class TestProbe:
    def test_no_token_is_not_a_failure(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        assert auth_state.probe_subscription_token() is True
        assert auth_state.subscription_stale() is False

    def test_a_good_token_clears_a_stale_flag(self, on_subscription, monkeypatch):
        class _Client:
            class messages:  # noqa: N801 - mirrors the SDK's attribute shape
                @staticmethod
                def count_tokens(**_kw):
                    return {"input_tokens": 3}

        class _LLM:
            model = "claude-sonnet-4-6"
            _client = _Client

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda: _LLM())
        auth_state.mark_subscription_stale("stale from a previous run")
        assert auth_state.probe_subscription_token() is True
        assert auth_state.subscription_stale() is False

    def test_an_auth_rejection_marks_it_stale(self, on_subscription, monkeypatch):
        def _boom():
            raise _auth_error()

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda: _boom())
        assert auth_state.probe_subscription_token() is False
        assert auth_state.subscription_stale() is True

    def test_being_offline_is_not_an_expired_token(self, on_subscription, monkeypatch):
        # Telling someone on a train that their credentials expired would be worse
        # than saying nothing, so only auth rejections count.
        def _boom():
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda: _boom())
        assert auth_state.probe_subscription_token() is True
        assert auth_state.subscription_stale() is False

    def test_background_probe_skips_when_not_on_subscription(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setattr(
            "yeaboi.auth_state.probe_subscription_token",
            lambda: pytest.fail("must not probe without subscription auth"),
        )
        auth_state.probe_in_background()  # returns without starting a thread


class TestProviderLabel:
    def test_known_provider(self):
        assert auth_state.provider_label("anthropic") == "Anthropic"
        assert auth_state.provider_label("bedrock") == "AWS Bedrock"

    def test_unknown_provider_falls_back_to_the_raw_value(self):
        assert auth_state.provider_label("mystery") == "mystery"

    def test_defaults_to_the_active_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        assert auth_state.provider_label() == "OpenAI"


class TestCheckLlmCredentials:
    """The provider-agnostic, synchronous liveness check the TUI gate calls."""

    def test_not_configured_short_circuits_before_any_network_call(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda *_a, **_kw: pytest.fail("must not ping when nothing is configured"),
        )

        status = auth_state.check_llm_credentials()

        assert status.ok is False
        assert status.configured is False
        assert status.provider_label == "Anthropic"
        assert status.reason  # non-empty, human-readable

    def test_configured_and_the_live_ping_succeeds(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-good")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", lambda *_a, **_kw: (True, "Key verified"))

        status = auth_state.check_llm_credentials()

        assert status.ok is True
        assert status.configured is True
        assert status.reason is None
        assert status.provider_label == "Anthropic"

    def test_configured_but_expired_reports_the_reason(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-expired")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key", lambda *_a, **_kw: (False, "Invalid API key")
        )

        status = auth_state.check_llm_credentials()

        assert status.ok is False
        assert status.configured is True
        assert status.reason == "Invalid API key"

    def test_subscription_auth_delegates_to_the_subscription_probe(self, on_subscription, monkeypatch):
        monkeypatch.setattr(auth_state, "probe_subscription_token", lambda: True)
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda *_a, **_kw: pytest.fail("subscription auth must not use the API-key ping"),
        )

        status = auth_state.check_llm_credentials()

        assert status.ok is True
        assert status.configured is True

    def test_subscription_auth_reports_staleness(self, on_subscription, monkeypatch):
        monkeypatch.setattr(auth_state, "probe_subscription_token", lambda: False)
        monkeypatch.setattr(auth_state, "stale_reason", lambda: "AuthenticationError")

        status = auth_state.check_llm_credentials()

        assert status.ok is False
        assert status.reason == "AuthenticationError"

    def test_non_anthropic_provider_pings_with_its_own_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-good")
        seen = {}

        def _fake_verify(provider, credential):
            seen["provider_val"] = provider["provider_val"]
            seen["credential"] = credential
            return True, "Key verified"

        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", _fake_verify)

        status = auth_state.check_llm_credentials()

        assert status.ok is True
        assert status.provider_label == "OpenAI"
        assert seen == {"provider_val": "openai", "credential": "sk-openai-good"}

    def test_it_pings_the_model_the_modes_will_actually_call(self, monkeypatch):
        # Not the verifier's hardcoded default: that proves the wrong thing, and
        # blocks a good key with a 404 the day that default retires.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-good")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-9")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        seen = {}

        def _fake_verify(provider, _credential):
            seen["model"] = (provider.get("models") or {}).get("default")
            return True, "Key verified"

        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", _fake_verify)

        auth_state.check_llm_credentials()

        assert seen["model"] == "claude-opus-4-9"


class TestInconclusiveChecksDoNotAccuse:
    """Being offline is not an expired key.

    `probe_subscription_token` has always refused to call an inconclusive probe
    a failure; this is the same rule for the provider-agnostic path, which
    otherwise reports "your API key looks invalid" to anyone on a train.
    """

    @pytest.fixture(autouse=True)
    def _on_api_key_auth(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fine")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)

    @pytest.mark.parametrize(
        "message",
        [
            "Connection error: [Errno 8] nodename nor servname provided",
            "Unexpected response: 503",
            "Unexpected response from Bedrock",
        ],
    )
    def test_an_inconclusive_answer_passes_the_user_through(self, monkeypatch, message):
        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", lambda *_a, **_kw: (False, message))

        status = auth_state.check_llm_credentials()

        assert status.ok is True
        assert status.reason is None

    @pytest.mark.parametrize("message", ["Invalid API key", "Key lacks permissions"])
    def test_a_definite_rejection_still_blocks(self, monkeypatch, message):
        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", lambda *_a, **_kw: (False, message))

        status = auth_state.check_llm_credentials()

        assert status.ok is False
        assert status.reason == message

    def test_a_local_ollama_failure_is_definite(self, monkeypatch):
        # A local server answers or it does not — no network to blame, and the
        # message names the fix.
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda *_a, **_kw: (False, "Ollama is installed but not running — start it with: ollama serve"),
        )

        status = auth_state.check_llm_credentials()

        assert status.ok is False
        assert "ollama serve" in status.reason

    def test_the_reason_is_redacted_before_it_reaches_a_screen(self, monkeypatch):
        # Google puts the API key in the request URL, so a provider message can
        # carry it. Logs go through RedactingFormatter; a rendered screen does not.
        monkeypatch.setenv("LLM_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTOPSECRETVALUE1234567890")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda *_a, **_kw: (False, "Invalid API key"),
        )
        monkeypatch.setattr(
            "yeaboi.redaction.redact", lambda text: text.replace("AIzaSyTOPSECRETVALUE1234567890", "***")
        )

        status = auth_state.check_llm_credentials()

        assert "AIzaSyTOPSECRETVALUE1234567890" not in (status.reason or "")


class TestCredentialCache:
    """A good answer is cached for the process; a bad one never is."""

    @pytest.fixture(autouse=True)
    def _on_api_key_auth(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-good")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)

    def _counting_verify(self, monkeypatch, result):
        calls = []
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda *_a, **_kw: (calls.append(1), result)[1],
        )
        return calls

    def test_a_good_result_is_not_re_pinged(self, monkeypatch):
        calls = self._counting_verify(monkeypatch, (True, "Key verified"))

        assert auth_state.check_llm_credentials().ok is True
        assert auth_state.check_llm_credentials().ok is True

        assert len(calls) == 1  # the happy path stays off the network

    def test_a_bad_result_is_re_pinged_every_time(self, monkeypatch):
        # A still-broken key must be reported every time it is still broken.
        calls = self._counting_verify(monkeypatch, (False, "Invalid API key"))

        assert auth_state.check_llm_credentials().ok is False
        assert auth_state.check_llm_credentials().ok is False

        assert len(calls) == 2

    def test_changing_the_credential_re_checks(self, monkeypatch):
        calls = self._counting_verify(monkeypatch, (True, "Key verified"))
        auth_state.check_llm_credentials()

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-rotated")
        auth_state.check_llm_credentials()

        assert len(calls) == 2  # the cache is keyed on the credential itself


class TestSettingsJump:
    """Ctrl+R is a request the hub claims, not a navigation the key performs."""

    def test_claimed_once(self):
        from yeaboi.ui.shared._music_bar import request_settings_jump, take_settings_jump

        take_settings_jump()  # drain anything left by another test
        assert take_settings_jump() is False
        request_settings_jump()
        assert take_settings_jump() is True
        assert take_settings_jump() is False  # only once


class TestStaleLine:
    def test_the_duck_names_the_shortcut(self):
        from yeaboi.ui.shared._music_bar import SUBSCRIPTION_STALE_LINE

        assert "ctrl+r" in SUBSCRIPTION_STALE_LINE
        # It has to say what is wrong as well as what to press.
        assert "subscription" in SUBSCRIPTION_STALE_LINE.lower()


class TestAcknowledgeSettling:
    """A key pressed before the result appeared must not dismiss it.

    Saving a token left the Subscription row focused underneath, where Enter means
    "sign in again" — so a leftover keystroke both skipped the confirmation and
    started minting a second token.
    """

    def test_settle_window_is_short_but_real(self):
        from yeaboi.ui.mode_select import _ACK_SETTLE_SECONDS

        # Long enough to swallow a paste tail or a double-tapped Enter, short
        # enough that a waiting user does not notice it.
        assert 0.15 <= _ACK_SETTLE_SECONDS <= 0.6

    def test_drain_is_safe_off_a_terminal(self):
        # pytest's stdin is not a tty; the drain must be a no-op, not a crash.
        from yeaboi.ui.shared._input import drain_pending_input

        drain_pending_input()

    def test_drain_flushes_a_real_tty(self):
        import os
        import pty
        import select

        from yeaboi.ui.shared._input import drain_pending_input

        class _Fd:
            """Lends the fd without owning it — a file object would close it."""

            def __init__(self, fd: int) -> None:
                self._fd = fd

            def fileno(self) -> int:
                return self._fd

        master, slave = pty.openpty()
        try:
            os.write(master, b"junk-that-should-never-be-read\r")
            select.select([slave], [], [], 1.0)  # wait for it to reach the input queue
            drain_pending_input(_Fd(slave))
            assert select.select([slave], [], [], 0)[0] == []  # nothing left to read
        finally:
            os.close(master)
            os.close(slave)
