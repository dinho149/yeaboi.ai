"""Sign-in.

The first cut of this took an email address and issued a session for it, which
is a name badge rather than authentication. What replaced it is a one-time
token, and the properties below are what make that worth the extra round trip.
Each one is a way the flow could look correct and not be.
"""

from __future__ import annotations

import json
import time

import pytest

from tests._app import call, sign_in
from yeaboi.app.auth import (
    LOGIN_RATE_LIMIT,
    LOGIN_TTL_SECONDS,
    InsecureDelivererError,
    LogDeliverer,
    LoginTokens,
    looks_like_email,
)
from yeaboi.app.server import AppServer
from yeaboi.app.store import AppStore


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


@pytest.fixture
def app(store):
    return AppServer(store)


@pytest.fixture
def logins(store):
    return LoginTokens(store)


class TestTokenLifecycle:
    def test_a_fresh_token_proves_its_address(self, logins):
        request = logins.request("ada@example.com")
        assert logins.consume(request.token) == "ada@example.com"

    def test_a_token_works_exactly_once(self, logins):
        request = logins.request("ada@example.com")
        assert logins.consume(request.token) == "ada@example.com"
        assert logins.consume(request.token) is None

    def test_an_expired_token_is_refused(self, logins, monkeypatch):
        request = logins.request("ada@example.com")
        import yeaboi.app.auth as auth_module

        # Capture the real clock first: a lambda that calls time.time() after
        # patching time.time is its own caller.
        later = time.time() + LOGIN_TTL_SECONDS + 1
        monkeypatch.setattr(auth_module.time, "time", lambda: later)
        assert logins.consume(request.token) is None

    def test_a_forged_token_is_refused(self, logins):
        logins.request("ada@example.com")
        assert logins.consume("obviously-not-it") is None

    def test_an_empty_token_is_refused(self, logins):
        assert logins.consume("") is None

    def test_two_addresses_get_different_tokens(self, logins):
        first = logins.request("ada@example.com")
        second = logins.request("bob@example.com")
        assert first.token != second.token


class TestTokensAreNotStoredInTheClear:
    """A stolen app.db must not hand over the ability to sign in as anyone."""

    def test_the_raw_token_is_nowhere_in_the_database(self, store, logins):
        request = logins.request("ada@example.com")
        with store._connect() as conn:  # noqa: SLF001
            rows = conn.execute("SELECT * FROM login_tokens").fetchall()
        blob = json.dumps([dict(row) for row in rows])
        assert request.token not in blob

    def test_what_is_stored_is_the_sha256(self, store, logins):
        import hashlib

        request = logins.request("ada@example.com")
        with store._connect() as conn:  # noqa: SLF001
            stored = conn.execute("SELECT token_hash FROM login_tokens").fetchone()["token_hash"]
        assert stored == hashlib.sha256(request.token.encode()).hexdigest()


class TestReplayIsDistinguishable:
    def test_a_spent_token_leaves_a_row_rather_than_vanishing(self, store, logins):
        # A replayed link and a forged one must be tellable apart, or neither
        # can be alerted on.
        request = logins.request("ada@example.com")
        logins.consume(request.token)
        with store._connect() as conn:  # noqa: SLF001
            row = conn.execute("SELECT used_at FROM login_tokens").fetchone()
        assert row["used_at"] is not None


class TestRateLimit:
    def test_a_burst_is_cut_off(self, logins):
        for _ in range(LOGIN_RATE_LIMIT):
            assert logins.request("ada@example.com") is not None
        assert logins.request("ada@example.com") is None

    def test_the_limit_is_per_address(self, logins):
        for _ in range(LOGIN_RATE_LIMIT):
            logins.request("ada@example.com")
        assert logins.request("bob@example.com") is not None


class TestEndpointsLeakNothing:
    def test_requesting_a_link_answers_the_same_for_unknown_addresses(self, app):
        # Otherwise this endpoint is a way to ask which addresses have accounts
        # — for a product used by named teams, that is a roster.
        known = call(app, "POST", "/api/auth/request", {"email": "ada@example.com"})
        call(app, "POST", "/api/auth/session", {"token": app.deliverer.delivered[-1].token})
        unknown = call(app, "POST", "/api/auth/request", {"email": "nobody@example.com"})
        assert known.code == unknown.code == 202
        assert known.body == unknown.body

    def test_a_rate_limited_request_is_indistinguishable_too(self, app):
        for _ in range(LOGIN_RATE_LIMIT + 2):
            response = call(app, "POST", "/api/auth/request", {"email": "ada@example.com"})
            assert response.code == 202

    def test_every_bad_token_gets_one_answer(self, app):
        # Expired, spent, forged and absent must not be distinguishable.
        forged = call(app, "POST", "/api/auth/session", {"token": "nope"})
        call(app, "POST", "/api/auth/request", {"email": "ada@example.com"})
        token = app.deliverer.delivered[-1].token
        call(app, "POST", "/api/auth/session", {"token": token})
        replayed = call(app, "POST", "/api/auth/session", {"token": token})
        assert forged.code == replayed.code == 401
        assert forged.body == replayed.body


class TestNoAccountUntilProven:
    def test_asking_for_a_link_creates_no_user(self, app):
        # Otherwise a stranger fills the table with addresses they do not own.
        call(app, "POST", "/api/auth/request", {"email": "ada@example.com"})
        assert app.store.user_by_email("ada@example.com") is None

    def test_redeeming_creates_the_user(self, app):
        sign_in(app, "ada@example.com")
        assert app.store.user_by_email("ada@example.com") is not None

    def test_an_unredeemed_link_grants_no_session(self, app):
        call(app, "POST", "/api/auth/request", {"email": "ada@example.com"})
        assert call(app, "GET", "/api/auth/me").code == 401


class TestTheDevDelivererStaysOnTheLaptop:
    def test_a_secure_deployment_refuses_to_log_sign_in_links(self, store):
        # secure_cookies is the closest signal for "not a laptop". Logging a
        # live credential there puts it in whatever ships logs off the box.
        with pytest.raises(InsecureDelivererError):
            AppServer(store, secure_cookies=True)

    def test_a_real_deliverer_is_accepted(self, store):
        assert AppServer(store, secure_cookies=True, deliverer=LogDeliverer()) is not None

    def test_the_default_is_the_dev_deliverer(self, app):
        assert isinstance(app.deliverer, LogDeliverer)


class TestEmailValidation:
    @pytest.mark.parametrize("email", ["ada@example.com", "a.b+c@sub.domain.co.uk"])
    def test_accepts_real_addresses(self, email):
        assert looks_like_email(email)

    @pytest.mark.parametrize("email", ["", "nope", "@example.com", "a@b", "a b@example.com"])
    def test_rejects_obvious_nonsense(self, email):
        assert not looks_like_email(email)


class TestHousekeeping:
    def test_purge_drops_only_expired_rows(self, logins, monkeypatch):
        logins.request("ada@example.com")
        import yeaboi.app.auth as auth_module

        later = time.time() + LOGIN_TTL_SECONDS + 1
        monkeypatch.setattr(auth_module.time, "time", lambda: later)
        assert logins.purge_expired() == 1
        assert logins.purge_expired() == 0
