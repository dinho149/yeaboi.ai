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
    yield
    auth_state.clear_subscription_stale()


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
