"""Tests for local Cloudflare Access JWT verification.

This is the file that decides whether the Access tier is a security control or a
decoration, so most of what follows is *denial* cases. A verifier that admits the
right people is easy; one that refuses everyone else under adversarial input is
the property worth pinning.

Three of these tests exist because they are the attacks a JWT verifier gets:
``alg: none``, a signature forged with a key we never published, and the HS256
confusion where the attacker signs with the RSA *public* key as an HMAC secret.
All three are killed by one line — ``algorithms=["RS256"]`` — which is exactly
why they are asserted rather than assumed.

A throwaway RSA keypair is generated once here and a fake JWKS is served from a
callable, so nothing touches the network.
"""

from __future__ import annotations

import json
import time

import pytest

jwt = pytest.importorskip("jwt", reason="the Access tier's PyJWT extra is optional")
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

from jwt.algorithms import RSAAlgorithm  # noqa: E402

from yeaboi.sharing.identity import (  # noqa: E402
    JWKS_REFRESH_FLOOR_SECONDS,
    AccessGate,
    AccessVerifier,
    VerifiedUser,
    _cookie,
    enforce_identity,
    gate_of,
    identity_required,
    preflight,
    verified_user,
)

TEAM = "acme"
ISSUER = f"https://{TEAM}.cloudflareaccess.com"
AUD = "aud-tag-for-this-application"
KID = "key-one"
HOSTNAME = "retro.example.com"


# One keypair for the whole module: 2048-bit generation is the slowest thing
# here by an order of magnitude, and every test wants the same "our" key.
_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(*keys: tuple[str, object]) -> dict:
    """A JWKS document for the given ``(kid, private_key)`` pairs."""
    out = []
    for kid, private in keys:
        entry = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
        entry["kid"] = kid
        entry["alg"] = "RS256"
        out.append(entry)
    return {"keys": out}


def _token(
    *,
    private: object = _PRIVATE,
    kid: str = KID,
    aud: str = AUD,
    issuer: str = ISSUER,
    sub: str = "subject-123",
    email: str = "Ada@Example.com",
    lifetime: int = 300,
    algorithm: str = "RS256",
    key: object | None = None,
) -> str:
    now = int(time.time())
    claims = {
        "aud": aud,
        "iss": issuer,
        "sub": sub,
        "email": email,
        "iat": now - 5,
        "exp": now + lifetime,
    }
    return jwt.encode(claims, key if key is not None else private, algorithm=algorithm, headers={"kid": kid})


def _verifier(document: dict | None = None, *, calls: list[str] | None = None) -> AccessVerifier:
    doc = document if document is not None else _jwks((KID, _PRIVATE))

    def fetch(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        return doc

    verifier = AccessVerifier(TEAM, AUD, fetch=fetch)
    verifier.warm()
    return verifier


class _FakeHandler:
    """A handler stand-in: headers and a server, which is all identity.py reads."""

    def __init__(self, headers: dict[str, str], gate: object | None = None) -> None:
        self.headers = headers
        self.server = type("S", (), {"access_gate": gate})()


class TestHappyPath:
    def test_a_valid_token_names_the_person(self):
        user = _verifier().verify({"Cf-Access-Jwt-Assertion": _token()})
        assert user == VerifiedUser(email="ada@example.com", subject="subject-123")

    def test_the_email_is_lowercased(self):
        # Cloudflare echoes whatever the IdP sent, and the admin allowlist is
        # compared case-insensitively — normalising once here is what lets
        # is_admin be a plain set membership test rather than a loop.
        user = _verifier().verify({"Cf-Access-Jwt-Assertion": _token(email="ADA@EXAMPLE.COM")})
        assert user is not None
        assert user.email == "ada@example.com"

    def test_the_cookie_is_read_when_the_header_is_absent(self):
        headers = {"Cookie": f"other=1; CF_Authorization={_token()}; more=2"}
        assert _verifier().verify(headers) is not None

    def test_the_header_wins_over_the_cookie(self):
        headers = {
            "Cf-Access-Jwt-Assertion": _token(sub="from-header"),
            "Cookie": f"CF_Authorization={_token(sub='from-cookie')}",
        }
        user = _verifier().verify(headers)
        assert user is not None
        assert user.subject == "from-header"


class TestDenials:
    """Every one of these must return None. None is the only refusal there is."""

    def test_no_token_at_all(self):
        assert _verifier().verify({}) is None

    def test_an_empty_token(self):
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": "   "}) is None

    def test_not_a_jwt(self):
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": "hello"}) is None

    def test_a_token_for_a_different_application(self):
        """The claim that binds a token to *this* app.

        Without the aud check, any token from the same Cloudflare team — issued
        for a different application, to a different audience — would verify here.
        """
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": _token(aud="some-other-app")}) is None

    def test_an_expired_token(self):
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": _token(lifetime=-60)}) is None

    def test_a_token_from_a_different_team(self):
        forged = _token(issuer="https://someone-else.cloudflareaccess.com")
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": forged}) is None

    def test_a_token_missing_a_required_claim(self):
        now = int(time.time())
        # No `sub`: nothing to key ownership on, so there is no identity here
        # even though the signature is ours.
        token = jwt.encode(
            {"aud": AUD, "iss": ISSUER, "iat": now, "exp": now + 300},
            _PRIVATE,
            algorithm="RS256",
            headers={"kid": KID},
        )
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": token}) is None

    def test_an_absurdly_long_token_is_not_even_parsed(self):
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": "a" * 9000}) is None


class TestTheThreeAttacks:
    """The three attacks a JWT verifier gets. All three must be refused.

    A note on *why* they are refused, because it took a mutation test to find
    out and the difference matters. Widening ``algorithms`` to
    ``["RS256", "HS256", "none"]`` does **not** make any of these pass: PyJWT
    also refuses ``alg: none`` when a key is supplied, and refuses to use an
    ``RSAPublicKey`` object as an HMAC secret. So these tests pin the *outcome*,
    which is what a caller depends on, and two independent mechanisms produce
    it.

    That is a good place to be — but it means these tests do not, on their own,
    hold the allowlist in place. :meth:`TestTheAlgorithmAllowlist` does that
    directly, and is the test that fails if someone removes it.
    """

    def test_an_unsigned_token_is_refused(self):
        now = int(time.time())
        token = jwt.encode(
            {"aud": AUD, "iss": ISSUER, "sub": "x", "iat": now, "exp": now + 300},
            key="",
            algorithm="none",
            headers={"kid": KID},
        )
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": token}) is None

    def test_a_signature_from_a_key_we_never_published_is_refused(self):
        # Correct claims, correct kid, real RS256 signature — by the wrong key.
        forged = _token(private=_OTHER)
        assert _verifier().verify({"Cf-Access-Jwt-Assertion": forged}) is None

    def test_algorithm_confusion_is_refused(self):
        """HS256 signed with the RSA *public* key as the HMAC secret.

        The classic verifier break: the public key is, by definition, public, so
        a verifier that honours the token's own ``alg`` lets an attacker mint
        anything. The allowlist means this token is rejected before its
        signature is ever considered.

        Hand-crafted rather than built with ``jwt.encode``, which refuses to
        produce it — PyJWT guards its *encoder* against this. An attacker has no
        such guard, so the test must not either.
        """
        import base64
        import hashlib
        import hmac

        from cryptography.hazmat.primitives import serialization

        public_pem = _PRIVATE.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        now = int(time.time())
        header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
        payload = b64(json.dumps({"aud": AUD, "iss": ISSUER, "sub": "x", "iat": now, "exp": now + 300}).encode())
        signing_input = header + b"." + payload
        signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
        token = (signing_input + b"." + signature).decode()

        assert _verifier().verify({"Cf-Access-Jwt-Assertion": token}) is None


class TestTheAlgorithmAllowlist:
    """The allowlist itself, pinned directly.

    Written as a white-box assertion on purpose. The behavioural tests above
    pass with or without it (PyJWT's own key typing also blocks those tokens),
    so nothing else in this file would notice its removal — and it is the line
    the module docstring names as the control. A structural test is the honest
    way to hold a defence-in-depth measure whose effect is invisible while the
    layer beneath it happens to work.
    """

    def test_verify_only_ever_accepts_rs256(self, monkeypatch):
        seen: dict = {}
        real = jwt.decode

        def spy(token, **kwargs):
            seen.update(kwargs)
            return real(token, **kwargs)

        monkeypatch.setattr(jwt, "decode", spy)
        _verifier().verify({"Cf-Access-Jwt-Assertion": _token()})
        assert seen["algorithms"] == ["RS256"]

    def test_verify_pins_the_audience_and_issuer(self, monkeypatch):
        seen: dict = {}
        real = jwt.decode

        def spy(token, **kwargs):
            seen.update(kwargs)
            return real(token, **kwargs)

        monkeypatch.setattr(jwt, "decode", spy)
        _verifier().verify({"Cf-Access-Jwt-Assertion": _token()})
        assert seen["audience"] == AUD
        assert seen["issuer"] == ISSUER
        assert set(seen["options"]["require"]) >= {"exp", "aud", "iss", "sub"}


class TestKeyRotation:
    def test_an_unknown_kid_triggers_a_refresh_once_the_floor_has_passed(self):
        calls: list[str] = []
        verifier = _verifier(calls=calls)
        assert len(calls) == 1  # the warm()
        verifier._attempted_at -= JWKS_REFRESH_FLOOR_SECONDS * 2  # 2 minutes have passed
        verifier.verify({"Cf-Access-Jwt-Assertion": _token(kid="rotated")})
        assert len(calls) == 2

    def test_an_unknown_kid_inside_the_floor_does_not_refetch_at_all(self):
        """The floor is measured from the last *attempt*, including a successful one.

        Measuring it from the last failure instead would leave the amplifier
        wide open: each fetch succeeds, so each unknown kid would be allowed to
        trigger the next one, and a stranger sending random kids would turn one
        inbound request into one outbound request to Cloudflare, forever.

        The cost is that a key rotation within a minute of a board opening is not
        picked up until the floor passes. That is the right way round — the
        visitor retries and it works.
        """
        calls: list[str] = []
        verifier = _verifier(calls=calls)
        for i in range(20):
            verifier.verify({"Cf-Access-Jwt-Assertion": _token(kid=f"random-{i}")})
        assert len(calls) == 1  # the warm() and nothing else

    def test_twenty_unknown_kids_cost_at_most_one_refresh(self):
        calls: list[str] = []
        verifier = _verifier(calls=calls)
        verifier._attempted_at -= JWKS_REFRESH_FLOOR_SECONDS * 2
        for i in range(20):
            verifier.verify({"Cf-Access-Jwt-Assertion": _token(kid=f"random-{i}")})
        assert len(calls) == 2

    def test_a_rotated_key_is_picked_up(self):
        documents = [_jwks((KID, _PRIVATE)), _jwks(("kid-2", _OTHER))]

        def fetch(url: str) -> dict:
            return documents[min(len(seen), len(documents) - 1)] if seen.append(url) is None else {}

        seen: list[str] = []
        verifier = AccessVerifier(TEAM, AUD, fetch=fetch)
        verifier.warm()
        verifier._attempted_at -= JWKS_REFRESH_FLOOR_SECONDS * 2
        # A token signed by the *new* key, whose kid we have never seen.
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token(private=_OTHER, kid="kid-2")}) is not None

    def test_a_retired_key_stops_working(self):
        """Replaced wholesale, not merged.

        A key Cloudflare has stopped publishing has been retired, and continuing
        to accept tokens signed with it is precisely what rotation is meant to
        end.
        """
        documents = [_jwks((KID, _PRIVATE)), _jwks(("kid-2", _OTHER))]
        state = {"n": 0}

        def fetch(url: str) -> dict:
            doc = documents[min(state["n"], len(documents) - 1)]
            state["n"] += 1
            return doc

        verifier = AccessVerifier(TEAM, AUD, fetch=fetch)
        verifier.warm()
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token()}) is not None
        verifier._refresh(force=True)
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token()}) is None


class TestFailingClosed:
    def test_a_cold_cache_denies_rather_than_admits(self):
        """The whole design in one assertion.

        The keys could not be fetched, so nothing can be checked — and the
        answer to "I cannot check this" is no, not yes.
        """

        def fetch(url: str) -> dict:
            raise OSError("network down")

        verifier = AccessVerifier(TEAM, AUD, fetch=fetch)
        assert verifier.warm() is False
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token()}) is None

    def test_a_warm_cache_survives_a_network_blip(self):
        # The other half of the trade: a transient failure must not eject a
        # live board's participants, so successfully-fetched keys are kept.
        state = {"fail": False}

        def fetch(url: str) -> dict:
            if state["fail"]:
                raise OSError("network down")
            return _jwks((KID, _PRIVATE))

        verifier = AccessVerifier(TEAM, AUD, fetch=fetch)
        verifier.warm()
        state["fail"] = True
        verifier._refresh(force=True)
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token()}) is not None

    def test_an_empty_key_set_is_not_treated_as_a_successful_fetch(self):
        verifier = AccessVerifier(TEAM, AUD, fetch=lambda url: {"keys": []})
        assert verifier.warm() is False

    def test_one_unparseable_key_does_not_lose_the_others(self):
        document = _jwks((KID, _PRIVATE))
        document["keys"].append({"kid": "broken", "kty": "RSA", "n": "!!!", "e": "AQAB"})
        verifier = AccessVerifier(TEAM, AUD, fetch=lambda url: document)
        assert verifier.warm() is True
        assert verifier.verify({"Cf-Access-Jwt-Assertion": _token()}) is not None


class TestTheLoopbackRule:
    """Which requests must verify — keyed on ``Host``, never on the socket.

    cloudflared connects from ``127.0.0.1``, so ``client_address`` cannot tell
    the host's own browser from a remote teammate. Requiring a token on every
    request would lock the host out of their own board; requiring it on none
    would be no control at all.
    """

    def _gate(self) -> AccessGate:
        return AccessGate(HOSTNAME, _verifier(), frozenset({"ada@example.com"}))

    def test_the_published_hostname_must_verify(self):
        assert self._gate().requires_verification({"Host": HOSTNAME}) is True

    def test_the_hostname_with_a_port_must_verify(self):
        assert self._gate().requires_verification({"Host": f"{HOSTNAME}:443"}) is True

    def test_loopback_does_not(self):
        for host in ("127.0.0.1:5173", "localhost:5173", "[::1]:5173"):
            assert self._gate().requires_verification({"Host": host}) is False, host

    def test_an_unknown_host_must_verify(self):
        # Fail closed: a request we cannot place is a request we do not trust.
        assert self._gate().requires_verification({"Host": "evil.example.net"}) is True

    def test_a_missing_host_must_verify(self):
        assert self._gate().requires_verification({}) is True


class TestAdminMembership:
    def _gate(self, *emails: str) -> AccessGate:
        return AccessGate(HOSTNAME, _verifier(), frozenset(emails))

    def test_an_exact_match_is_admin(self):
        assert self._gate("ada@example.com").is_admin(VerifiedUser("ada@example.com", "s")) is True

    def test_the_match_is_case_insensitive(self):
        assert self._gate("ada@example.com").is_admin(VerifiedUser("ADA@Example.com", "s")) is True

    def test_a_lookalike_domain_is_not_admin(self):
        """The reason this is set membership and never a substring test.

        ``ada@example.com in "ada@example.com,bob@example.com"`` is True — and so
        is ``"ada@example.com" in "ada@example.com.evil.net"`` for the reversed
        test. Either mistake hands host powers, including the microphone, to an
        attacker who controls a lookalike domain.
        """
        gate = self._gate("ada@example.com")
        assert gate.is_admin(VerifiedUser("ada@example.com.evil.net", "s")) is False
        assert gate.is_admin(VerifiedUser("bada@example.com", "s")) is False

    def test_nobody_is_admin_when_the_allowlist_is_empty(self):
        assert self._gate().is_admin(VerifiedUser("ada@example.com", "s")) is False

    def test_an_unverified_request_is_not_admin(self):
        assert self._gate("ada@example.com").is_admin(None) is False


class TestParticipantIdentity:
    def test_the_pid_is_namespaced(self):
        """It must be impossible for a client to *choose* a value this could equal.

        Card ownership is keyed on the pid, and the quick tier's is a
        browser-minted UUID. The ``cf:`` prefix is what guarantees the two
        vocabularies cannot overlap.
        """
        assert VerifiedUser("ada@example.com", "abc-123").pid == "cf:abc-123"

    def test_the_display_name_is_the_email_local_part(self):
        assert VerifiedUser("ada@example.com", "s").display_name == "ada"

    def test_a_verified_request_overrides_what_the_client_claimed(self):
        gate = AccessGate(HOSTNAME, _verifier(), frozenset())
        handler = _FakeHandler({"Host": HOSTNAME, "Cf-Access-Jwt-Assertion": _token()}, gate)
        pid, name = enforce_identity(handler, "i-am-someone-else", "Impersonator")
        assert pid == "cf:subject-123"
        assert name == "ada"

    def test_the_quick_tier_passes_the_client_values_straight_through(self):
        handler = _FakeHandler({"Host": "127.0.0.1:5173"}, None)
        assert enforce_identity(handler, "browser-uuid", "Ada") == ("browser-uuid", "Ada")

    def test_the_hosts_own_loopback_request_is_untouched(self):
        gate = AccessGate(HOSTNAME, _verifier(), frozenset())
        handler = _FakeHandler({"Host": "127.0.0.1:5173"}, gate)
        assert enforce_identity(handler, "browser-uuid", "Ada") == ("browser-uuid", "Ada")


class TestHandlerHelpers:
    def test_gate_of_is_none_without_a_server(self):
        assert gate_of(object()) is None

    def test_identity_is_memoized_per_token(self):
        calls: list[str] = []
        verifier = _verifier(calls=calls)
        gate = AccessGate(HOSTNAME, verifier, frozenset())
        handler = _FakeHandler({"Host": HOSTNAME, "Cf-Access-Jwt-Assertion": _token(kid="unknown-1")}, gate)
        verified_user(handler)
        before = len(calls)
        for _ in range(5):
            verified_user(handler)
        assert len(calls) == before  # the same token is not re-verified

    def test_the_memo_expires_so_a_tokens_exp_is_re_judged(self, monkeypatch):
        """A board's browser holds one keep-alive connection through 25 s long
        polls for a whole ceremony. An unbounded memo would keep serving a token
        whose exp passed mid-session — the memo must age out and re-verify."""
        from yeaboi.sharing import identity as identity_mod

        class _CountingGate:
            verifies = 0

            def requires_verification(self, headers):
                return True

            def verify(self, headers):
                self.verifies += 1
                return VerifiedUser(email="ada@example.com", subject="sub")

        gate = _CountingGate()
        handler = _FakeHandler({"Host": HOSTNAME, "Cf-Access-Jwt-Assertion": "tok"}, gate)
        assert verified_user(handler) is not None
        assert verified_user(handler) is not None
        assert gate.verifies == 1  # memoized while fresh
        # Age the cached entry past the memo window; the next call re-verifies.
        token, user, stamp = handler._identity_cache
        handler._identity_cache = (token, user, stamp - identity_mod._IDENTITY_MEMO_SECONDS - 1)
        verified_user(handler)
        assert gate.verifies == 2

    def test_a_different_token_on_the_same_connection_re_verifies(self):
        """One handler instance serves every request on a keep-alive connection.

        Caching "the identity for this handler" rather than "the identity for
        this token" would let the first request on a connection decide who every
        later one is.
        """
        gate = AccessGate(HOSTNAME, _verifier(), frozenset())
        handler = _FakeHandler({"Host": HOSTNAME, "Cf-Access-Jwt-Assertion": _token(sub="first")}, gate)
        assert verified_user(handler).subject == "first"
        handler.headers["Cf-Access-Jwt-Assertion"] = _token(sub="second")
        assert verified_user(handler).subject == "second"

    def test_identity_required_is_false_without_a_gate(self):
        assert identity_required(_FakeHandler({"Host": "anything"}, None)) is False


class TestCookieParsing:
    def test_one_malformed_pair_does_not_lose_the_header(self):
        """``SimpleCookie`` drops the *entire* header on any malformed pair.

        A stray cookie from an unrelated app on the same hostname would then log
        everyone out, which is why this is hand-parsed.
        """
        assert _cookie("broken; CF_Authorization=abc; =; x=1", "CF_Authorization") == "abc"

    def test_a_missing_cookie_is_empty(self):
        assert _cookie("a=1; b=2", "CF_Authorization") == ""


class TestPreflight:
    """A partial config is a loud failure, never a fall back to a public tunnel."""

    def _env(self, monkeypatch, **overrides: str) -> None:
        base = {
            "YEABOI_SHARE_MODE": "access",
            "CLOUDFLARE_TUNNEL_ID": "uuid-1",
            "CLOUDFLARE_TUNNEL_CREDENTIALS": "/nope/creds.json",
            "CLOUDFLARE_ACCESS_HOSTNAME": HOSTNAME,
            "CLOUDFLARE_ACCESS_TEAM": TEAM,
            "CLOUDFLARE_ACCESS_AUD": AUD,
            "CLOUDFLARE_ACCESS_ADMIN_EMAILS": "ada@example.com",
        }
        base.update(overrides)
        for key in (
            *base,
            "CLOUDFLARE_ACCESS_HOSTNAME_RETRO",
            "CLOUDFLARE_ACCESS_HOSTNAME_POKER",
            "CLOUDFLARE_ACCESS_HOSTNAME_SHARE",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, value in base.items():
            if value:
                monkeypatch.setenv(key, value)

    def test_the_tier_off_is_not_an_error(self, monkeypatch):
        for key in (
            "YEABOI_SHARE_MODE",
            "CLOUDFLARE_TUNNEL_ID",
            "CLOUDFLARE_TUNNEL_CREDENTIALS",
            "CLOUDFLARE_ACCESS_HOSTNAME",
            "CLOUDFLARE_ACCESS_HOSTNAME_RETRO",
            "CLOUDFLARE_ACCESS_TEAM",
            "CLOUDFLARE_ACCESS_AUD",
            "CLOUDFLARE_ACCESS_ADMIN_EMAILS",
        ):
            monkeypatch.delenv(key, raising=False)
        assert preflight("retro") == (None, "")

    def test_configured_but_never_switched_on_says_so(self, monkeypatch):
        """The shape of a host who followed the setup and missed the last line."""
        self._env(monkeypatch, YEABOI_SHARE_MODE="")
        gate, reason = preflight("retro")
        assert gate is None
        assert "verified users" in reason and "quick" in reason

    def test_an_explicit_quick_choice_with_stored_config_stays_silent(self, monkeypatch):
        """Switching back to quick in Settings writes the mode explicitly — that
        host chose public links and must not be nagged or refused."""
        self._env(monkeypatch, YEABOI_SHARE_MODE="quick")
        assert preflight("retro") == (None, "")

    @pytest.mark.parametrize(
        "missing",
        [
            "CLOUDFLARE_TUNNEL_ID",
            "CLOUDFLARE_TUNNEL_CREDENTIALS",
            "CLOUDFLARE_ACCESS_TEAM",
            "CLOUDFLARE_ACCESS_AUD",
        ],
    )
    def test_a_missing_key_is_named(self, monkeypatch, missing):
        self._env(monkeypatch, **{missing: ""})
        gate, reason = preflight("retro")
        assert gate is None
        assert missing in reason

    def test_a_missing_hostname_is_named(self, monkeypatch):
        self._env(monkeypatch, CLOUDFLARE_ACCESS_HOSTNAME="")
        gate, reason = preflight("retro")
        assert gate is None
        assert "CLOUDFLARE_ACCESS_HOSTNAME" in reason

    def test_a_credentials_file_that_is_not_there_is_named(self, monkeypatch, tmp_path):
        self._env(monkeypatch, CLOUDFLARE_TUNNEL_CREDENTIALS=str(tmp_path / "absent.json"))
        gate, reason = preflight("retro")
        assert gate is None
        assert "credentials file not found" in reason

    def test_unreachable_keys_refuse_to_publish(self, monkeypatch, tmp_path):
        """Not a warning — a refusal.

        Publishing a hostname whose tokens we cannot verify means every request
        403s anyway. Better to not publish it and say why.
        """
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        self._env(monkeypatch, CLOUDFLARE_TUNNEL_CREDENTIALS=str(creds))
        monkeypatch.setattr(
            "yeaboi.sharing.identity._fetch_json",
            lambda url: (_ for _ in ()).throw(OSError("no network")),
        )
        gate, reason = preflight("retro")
        assert gate is None
        assert "signing keys" in reason

    def test_a_complete_config_builds_a_gate(self, monkeypatch, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        self._env(monkeypatch, CLOUDFLARE_TUNNEL_CREDENTIALS=str(creds))
        monkeypatch.setattr("yeaboi.sharing.identity._fetch_json", lambda url: _jwks((KID, _PRIVATE)))
        gate, reason = preflight("retro")
        assert reason == ""
        assert gate is not None
        assert gate.hostname == HOSTNAME
        assert gate.admin_emails == frozenset({"ada@example.com"})

    def test_the_per_surface_hostname_wins(self, monkeypatch, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        self._env(monkeypatch, CLOUDFLARE_TUNNEL_CREDENTIALS=str(creds))
        monkeypatch.setenv("CLOUDFLARE_ACCESS_HOSTNAME_POKER", "poker.example.com")
        monkeypatch.setattr("yeaboi.sharing.identity._fetch_json", lambda url: _jwks((KID, _PRIVATE)))
        gate, _ = preflight("poker")
        assert gate is not None
        assert gate.hostname == "poker.example.com"
