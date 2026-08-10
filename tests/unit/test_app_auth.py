"""Sign-in.

The first cut of this took an email address and issued a session for it, which
is a name badge rather than authentication. What replaced it is a one-time
token, and the properties below are what make that worth the extra round trip.
Each one is a way the flow could look correct and not be.
"""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace
from unittest import mock

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
from yeaboi.app.server import AppRequestHandler, AppServer
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


class TestSmtpDeliverer:
    """The mail path. No new dependency, and no second set of env vars."""

    def _deliverer(self, **kw):
        from yeaboi.app.auth import SmtpDeliverer

        defaults = {"host": "smtp.example.com", "port": 587, "user": "u", "password": "p", "sender": "no-reply@x.com"}
        return SmtpDeliverer(kw.pop("base_url", "https://app.example.com"), **{**defaults, **kw})

    def test_the_link_is_absolute_and_carries_the_token(self):
        from yeaboi.app.auth import LoginRequest

        deliverer = self._deliverer()
        link = deliverer.link(LoginRequest(email="a@b.com", token="tok123", expires_at=0))
        assert link == "https://app.example.com/signin?token=tok123"

    def test_a_token_needing_escaping_is_escaped(self):
        from yeaboi.app.auth import LoginRequest

        link = self._deliverer().link(LoginRequest(email="a@b.com", token="a/b+c", expires_at=0))
        assert "a/b+c" not in link.split("token=")[1] or "%" in link

    def test_a_relative_base_url_is_refused(self):
        # The Host header is attacker-controlled, so this cannot be derived
        # from the request - a guessed base is a link pointing anywhere.
        with pytest.raises(ValueError, match="absolute"):
            self._deliverer(base_url="app.example.com")

    def test_no_smtp_host_is_refused_at_construction(self):
        # Better to fail at startup than to accept sign-ins that silently
        # never arrive.
        with pytest.raises(ValueError, match="SMTP host"):
            self._deliverer(host="")

    def test_it_sends_over_starttls_and_logs_no_token(self, caplog):
        import smtplib

        from yeaboi.app.auth import LoginRequest

        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=0):
                sent["endpoint"] = (host, port)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                sent.setdefault("ehlo", 0)
                sent["ehlo"] += 1

            def has_extn(self, name):
                return name == "STARTTLS"

            def starttls(self):
                sent["starttls"] = True

            def login(self, user, password):
                sent["login"] = user

            def send_message(self, message):
                sent["to"] = message["To"]
                sent["body"] = message.get_content()

        with mock.patch.object(smtplib, "SMTP", FakeSMTP), caplog.at_level("INFO"):
            self._deliverer().deliver(LoginRequest(email="ada@example.com", token="secret-token", expires_at=0))

        assert sent["starttls"] is True
        assert sent["login"] == "u"
        assert sent["to"] == "ada@example.com"
        assert "secret-token" in sent["body"]
        # A live credential must not end up in the logs.
        assert "secret-token" not in caplog.text

    def test_it_satisfies_the_deliverer_protocol(self, store):
        from yeaboi.app.server import AppServer

        # The point of the protocol: a secure deployment accepts this one.
        assert AppServer(store, secure_cookies=True, deliverer=self._deliverer()) is not None


class TestBuildDeliverer:
    """`serve()` picks a deliverer from the environment."""

    def test_no_base_url_means_the_dev_deliverer(self, monkeypatch):
        from yeaboi.app.server import build_deliverer

        monkeypatch.delenv("YEABOI_APP_BASE_URL", raising=False)
        assert build_deliverer() is None

    def test_a_half_configured_environment_warns_rather_than_refusing_to_boot(self, monkeypatch, caplog):
        # A server that will not start because email is half-configured is
        # worse than one that starts and says sign-in cannot be delivered.
        from yeaboi.app.server import build_deliverer

        monkeypatch.setenv("YEABOI_APP_BASE_URL", "https://app.example.com")
        monkeypatch.setenv("STANDUP_SMTP_HOST", "")
        with caplog.at_level("WARNING"):
            assert build_deliverer() is None
        assert "not configured" in caplog.text

    def test_a_configured_environment_builds_the_smtp_deliverer(self, monkeypatch):
        from yeaboi.app.auth import SmtpDeliverer
        from yeaboi.app.server import build_deliverer

        monkeypatch.setenv("YEABOI_APP_BASE_URL", "https://app.example.com")
        monkeypatch.setenv("STANDUP_SMTP_HOST", "smtp.example.com")
        assert isinstance(build_deliverer(), SmtpDeliverer)


class TestTheDevLinkIsActuallyVisible:
    """Regression: the dev link went to a logger nothing had configured.

    `yeaboi app` calls no `basicConfig`, so `logger.warning` was swallowed and
    the sign-in link was never shown — meaning sign-in was impossible on a
    laptop, which is the default path. Found by running the server rather than
    by any test, which is why there is now a test.
    """

    def test_the_link_is_written_to_the_stream_without_logging_configured(self):
        import io
        import logging

        from yeaboi.app.auth import LogDeliverer, LoginRequest

        stream = io.StringIO()
        deliverer = LogDeliverer(stream)
        # Silence logging entirely: the link must still appear.
        logging.disable(logging.CRITICAL)
        try:
            deliverer.deliver(LoginRequest(email="ada@example.com", token="tok-visible", expires_at=0))
        finally:
            logging.disable(logging.NOTSET)
        assert "tok-visible" in stream.getvalue()
        assert "ada@example.com" in stream.getvalue()

    def test_it_still_records_what_it_delivered(self):
        import io

        from yeaboi.app.auth import LogDeliverer, LoginRequest

        deliverer = LogDeliverer(io.StringIO())
        deliverer.deliver(LoginRequest(email="a@b.com", token="t", expires_at=0))
        assert deliverer.delivered[-1].token == "t"


class TestHeadIsAnswered:
    """Regression: the stdlib handler answers 501 for a verb it has no method
    for, so a monitor probing with HEAD reported the service as broken while a
    browser saw it fine.

    Driven against the real handler class with a fake socket, because the bug
    lived in the HTTP layer rather than in `AppServer.handle` — which is
    exactly the seam the rest of the suite bypasses.
    """

    def _raw_response(self, app, method: str, path: str) -> str:
        handler = AppRequestHandler.__new__(AppRequestHandler)
        handler.server = SimpleNamespace(app=app)
        handler.wfile = io.BytesIO()
        handler.rfile = io.BytesIO()
        handler.path = path
        handler.headers = {}
        handler.request_version = "HTTP/1.1"
        # send_response logs the request line, so it has to exist.
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.command = method
        handler.client_address = ("127.0.0.1", 0)
        handler.log_message = lambda *a, **k: None
        getattr(handler, f"do_{method}")()
        return handler.wfile.getvalue().decode("latin-1")

    def test_head_answers_200_with_a_content_length_and_no_body(self, app):
        raw = self._raw_response(app, "HEAD", "/api/health")
        assert " 200 " in raw.splitlines()[0]
        assert "Content-Length: 15" in raw
        _, _, body = raw.partition("\r\n\r\n")
        assert body == ""

    def test_head_carries_the_same_security_headers_as_get(self, app):
        head = self._raw_response(app, "HEAD", "/")
        assert "Content-Security-Policy:" in head

    def test_get_still_carries_its_body(self, app):
        raw = self._raw_response(app, "GET", "/api/health")
        assert '{"status":"ok"}' in raw


class TestFirstRunClaim:
    """Claiming a fresh local instance from the browser, with no email.

    It exists because sign-in links go by email and a laptop has no SMTP —
    without it, a browser-only user of a fresh instance cannot get in at all.
    Which makes the gating the whole story: three conditions, each closing a
    different door, and every one of them tested here.
    """

    def test_a_fresh_local_instance_is_claimable(self, app):
        assert json.loads(call(app, "GET", "/api/auth/first-run").body)["available"] is True

    def test_claiming_creates_the_user_and_signs_them_in(self, app):
        response = call(app, "POST", "/api/auth/claim", {"email": "ada@example.com"})
        assert response.code == 201
        assert app.store.user_by_email("ada@example.com") is not None
        jar = "; ".join(
            value.split(";")[0] for key, value in response.headers if key == "Set-Cookie"
        )
        assert json.loads(call(app, "GET", "/api/auth/me", cookies=jar).body)["email"] == "ada@example.com"

    def test_it_closes_permanently_once_a_user_exists(self, app):
        # Otherwise it is a way in past sign-in, forever.
        call(app, "POST", "/api/auth/claim", {"email": "ada@example.com"})
        assert json.loads(call(app, "GET", "/api/auth/first-run").body)["available"] is False
        assert call(app, "POST", "/api/auth/claim", {"email": "eve@example.com"}).code == 403

    def test_a_remote_request_cannot_claim(self, app):
        from yeaboi.app.router import parse_request

        remote = parse_request(
            "POST", "/api/auth/claim", {}, json.dumps({"email": "eve@example.com"}).encode(), client_host="10.0.0.9"
        )
        assert app.handle(remote).code == 403
        assert app.store.count_users() == 0

    def test_a_forwarded_header_cannot_fake_being_local(self, app):
        # The peer address comes off the socket; a header is caller-supplied
        # and would make this claimable by anyone who says the right words.
        from yeaboi.app.router import parse_request

        spoofed = parse_request(
            "POST",
            "/api/auth/claim",
            {"X-Forwarded-For": "127.0.0.1"},
            json.dumps({"email": "eve@example.com"}).encode(),
            client_host="10.0.0.9",
        )
        assert app.handle(spoofed).code == 403

    def test_a_secure_deployment_is_never_claimable(self, store):
        # Hosted must go through email, or the first stranger to find the URL
        # owns the instance.
        secure = AppServer(store, secure_cookies=True, deliverer=LogDeliverer())
        assert json.loads(call(secure, "GET", "/api/auth/first-run").body)["available"] is False
        assert call(secure, "POST", "/api/auth/claim", {"email": "eve@example.com"}).code == 403

    def test_a_bad_email_is_still_refused(self, app):
        assert call(app, "POST", "/api/auth/claim", {"email": "nope"}).code == 400

    def test_the_refusal_does_not_say_which_condition_failed(self, app, store):
        # "already claimed", "not local" and "hosted" are one answer.
        call(app, "POST", "/api/auth/claim", {"email": "ada@example.com"})
        claimed = call(app, "POST", "/api/auth/claim", {"email": "eve@example.com"})
        secure = AppServer(store, secure_cookies=True, deliverer=LogDeliverer())
        hosted = call(secure, "POST", "/api/auth/claim", {"email": "eve@example.com"})
        assert claimed.body == hosted.body


class TestSignOutEverywhere:
    """The control that makes a session revocable from a device you still have.

    `revoke_all` existed from the substrate commit with nothing calling it,
    which made it a promise the product did not keep.
    """

    def test_it_kills_every_session_not_just_this_one(self, app):
        first, first_csrf = sign_in(app, "ada@example.com")
        second, _ = sign_in(app, "ada@example.com")
        # Both are live before.
        assert call(app, "GET", "/api/auth/me", cookies=first).code == 200
        assert call(app, "GET", "/api/auth/me", cookies=second).code == 200

        assert call(app, "DELETE", "/api/auth/sessions", cookies=first, csrf=first_csrf).code == 200

        assert call(app, "GET", "/api/auth/me", cookies=first).code == 401
        assert call(app, "GET", "/api/auth/me", cookies=second).code == 401

    def test_it_clears_the_calling_browser_s_cookie(self, app):
        # Otherwise the browser holds a token the server has forgotten, and the
        # next request reads as a mysterious sign-out.
        cookies, csrf = sign_in(app)
        response = call(app, "DELETE", "/api/auth/sessions", cookies=cookies, csrf=csrf)
        cleared = [value for key, value in response.headers if key == "Set-Cookie"]
        assert cleared and all("Max-Age=0" in value for value in cleared)

    def test_it_leaves_other_users_alone(self, app):
        ada, ada_csrf = sign_in(app, "ada@example.com")
        bob, _ = sign_in(app, "bob@example.com")
        call(app, "DELETE", "/api/auth/sessions", cookies=ada, csrf=ada_csrf)
        assert call(app, "GET", "/api/auth/me", cookies=bob).code == 200

    def test_it_needs_a_session(self, app):
        assert call(app, "DELETE", "/api/auth/sessions").code == 401

    def test_it_needs_the_csrf_header(self, app):
        cookies, _ = sign_in(app)
        assert call(app, "DELETE", "/api/auth/sessions", cookies=cookies).code == 403


class TestExpiredTokensDoNotAccumulate:
    """The table only grows when a link is asked for, so that is where it is swept."""

    def _count(self, store):
        with store._connect() as conn:  # noqa: SLF001
            return int(conn.execute("SELECT COUNT(*) AS n FROM login_tokens").fetchone()["n"])

    def test_requesting_a_link_clears_out_dead_rows(self, store, logins, monkeypatch):
        import yeaboi.app.auth as auth_module

        logins.request("old@example.com")
        assert self._count(store) == 1

        later = time.time() + LOGIN_TTL_SECONDS + 1
        monkeypatch.setattr(auth_module.time, "time", lambda: later)
        logins.request("new@example.com")

        # The dead one is gone; the fresh one remains.
        assert self._count(store) == 1

    def test_a_live_token_is_never_swept(self, store, logins):
        first = logins.request("ada@example.com")
        logins.request("bob@example.com")
        assert logins.consume(first.token) == "ada@example.com"

    def test_sweeping_does_not_resurrect_the_rate_limit(self, logins, monkeypatch):
        # The limit counts recent requests; a sweep of *expired* rows must not
        # be a way to reset it, since the window is shorter than the TTL.
        for _ in range(LOGIN_RATE_LIMIT):
            logins.request("ada@example.com")
        assert logins.request("ada@example.com") is None


class TestFirstRunInAContainer:
    """A published port means traffic arrives from the Docker bridge.

    Without the opt-in, first-run setup is unavailable in exactly the
    deployment it exists for; with it treated as a header sniff, anyone could
    claim the instance. So it is an explicit flag that waives one condition.
    """

    def _remote(self, app, path="/api/auth/claim", body=None):
        from yeaboi.app.router import parse_request

        raw = json.dumps(body or {"email": "ada@example.com"}).encode()
        return app.handle(parse_request("POST", path, {}, raw, client_host="172.18.0.1"))

    def test_a_bridge_address_cannot_claim_by_default(self, store):
        app = AppServer(store)
        assert self._remote(app).code == 403

    def test_the_opt_in_allows_it(self, store):
        app = AppServer(store, allow_remote_first_run=True)
        assert self._remote(app).code == 201
        assert app.store.user_by_email("ada@example.com") is not None

    def test_the_opt_in_does_not_survive_a_secure_deployment(self, store):
        # The flag waives the locality condition and nothing else: a TLS
        # deployment must still go through email, or the first stranger to
        # find the URL owns it.
        app = AppServer(
            store, secure_cookies=True, deliverer=LogDeliverer(), allow_remote_first_run=True
        )
        assert self._remote(app).code == 403

    def test_the_opt_in_still_closes_once_claimed(self, store):
        app = AppServer(store, allow_remote_first_run=True)
        assert self._remote(app).code == 201
        assert self._remote(app, body={"email": "eve@example.com"}).code == 403
