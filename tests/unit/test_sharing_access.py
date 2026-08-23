"""Unit tests for the shared access credentials and the participant-URL rule.

The interesting one is :func:`participant_url`. Both live boards bind loopback
and are shared only through a Cloudflare tunnel, so "which address do we hand
out" has exactly one right answer and two wrong ones — the host's own
``127.0.0.1``, and nothing at all when a link genuinely exists.
"""

from urllib.parse import urlparse

import pytest

from yeaboi.sharing import access
from yeaboi.sharing.access import (
    JoinLimiter,
    invite_payload,
    invite_url,
    make_join_code,
    make_token,
    participant_url,
)

TUNNEL = "https://abc-def.trycloudflare.com/"
CODE = "K3P9-2QXA"


class TestCredentials:
    def test_token_is_long_and_unique(self):
        assert len(make_token()) >= 16
        assert make_token() != make_token()

    def test_join_code_is_grouped_and_unambiguous(self):
        code = make_join_code()
        assert len(code) == 9 and code[4] == "-"
        # No 0/O/1/I: the code is read off a screen and typed by someone else.
        assert not set(code[:4] + code[5:]) & set("01OI")


class TestParticipantUrl:
    def test_public_url_wins_outright(self):
        # The host's own request arrives on loopback, so deriving from it would
        # hand a teammate an address pointing at their own machine.
        assert participant_url({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", TUNNEL) == TUNNEL

    def test_derives_from_the_request_when_there_is_no_public_url(self):
        # Still correct for a visitor who arrived *through* the tunnel.
        headers = {"Host": "abc-def.trycloudflare.com", "X-Forwarded-Proto": "https"}
        assert participant_url(headers, "127.0.0.1:5173") == TUNNEL

    def test_https_only_when_the_proxy_says_so(self):
        assert participant_url({"Host": "board.example"}, "x") == "http://board.example/"

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1:5173", "127.0.0.1", "127.1.2.3:80", "localhost:5273", "LOCALHOST", "[::1]:5173"],
    )
    def test_returns_nothing_rather_than_a_loopback_address(self, host):
        # Nothing is better than 127.0.0.1 here: the browser's invite panel copies
        # whatever this returns the moment it opens, and every consumer already
        # renders an empty value as "not ready" instead of as a link.
        assert participant_url({"Host": host}, "127.0.0.1:5173") == ""

    def test_the_fallback_host_is_checked_too(self):
        # No Host header at all (HTTP/1.0 client) falls back to the bind address,
        # which on these servers is always loopback.
        assert participant_url({}, "127.0.0.1:5173") == ""

    def test_a_routable_host_is_still_returned(self):
        # Only loopback is suppressed. A board reached on any other name is being
        # reached by something that is not this process.
        assert participant_url({"Host": "board.example:8712"}, "x") == "http://board.example:8712/"


class TestInviteUrl:
    """The one link that is the whole invite.

    The bug this function exists to make impossible: every copy path used to put
    ``f"{url}\\nAccess code: {code}"`` on the clipboard, and any paste target that
    flattens a newline turned it into ``…/%20Access%20code:%207PER-8G5F`` — a
    path no server serves, so the reader got a 404 and assumed the share was
    broken.

    ``JoinGate.readInviteCode`` in ``frontend/src/shared/JoinGate.tsx`` is the
    other half of this contract and has a mirroring suite; the fragment format
    is the one thing the two files must agree on.
    """

    def test_one_string_carries_both_halves(self):
        assert invite_url(TUNNEL, CODE) == f"https://abc-def.trycloudflare.com/#code={CODE}"

    def test_the_code_rides_in_the_fragment_and_never_the_query(self):
        # A fragment is never sent to the origin, which is the whole reason for
        # this shape: the code stays out of cloudflared's access log and out of
        # the Referer when the visitor clicks the credit link on the gate.
        parsed = urlparse(invite_url(TUNNEL, CODE))
        assert parsed.fragment == f"code={CODE}"
        assert parsed.query == ""
        assert "?code=" not in invite_url(TUNNEL, CODE)

    @pytest.mark.parametrize(
        "share",
        [
            "https://abc-def.trycloudflare.com",
            "https://abc-def.trycloudflare.com/",
            "https://abc-def.trycloudflare.com//",
        ],
    )
    def test_exactly_one_slash_however_the_caller_spelled_it(self, share):
        # Four producers append their own trailing slash independently (both
        # board tunnel workers, the output-share worker, participant_url's
        # fallback). Two of them agreeing to append is how you get `//#code=`.
        assert invite_url(share, CODE) == f"https://abc-def.trycloudflare.com/#code={CODE}"

    def test_a_path_prefix_survives(self):
        # Matters behind a reverse proxy: the board is not always at the root.
        assert invite_url("https://co.example/board/", CODE) == f"https://co.example/board/#code={CODE}"

    def test_a_query_string_stays_a_query_string(self):
        # No producer sends one today, but this is a public function with four
        # callers: concatenating would bury the fragment inside the query.
        assert invite_url("https://co.example/?ref=chat", CODE) == f"https://co.example/?ref=chat#code={CODE}"

    def test_an_existing_fragment_is_replaced_not_appended(self):
        assert invite_url("https://co.example/#stale", CODE) == f"https://co.example/#code={CODE}"

    @pytest.mark.parametrize(("share", "code"), [("", CODE), (TUNNEL, ""), ("", "")])
    def test_nothing_at_all_rather_than_half_an_invite(self, share, code):
        # participant_url already answers "" before the tunnel is up, and every
        # caller renders that as "the secure link is still starting" rather than
        # as a link. Propagating it keeps that one branch doing the work.
        assert invite_url(share, code) == ""

    def test_a_real_code_needs_no_escaping(self):
        # The join alphabet is A-Z minus O/I plus 2-9, so the code can go into a
        # fragment verbatim. If that alphabet ever grows, this fails first.
        code = make_join_code()
        assert urlparse(invite_url(TUNNEL, code)).fragment == f"code={code}"


class TestInvitePayload:
    def test_carries_the_code_and_the_public_url(self):
        body = invite_payload({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", CODE, TUNNEL)
        assert body == {
            "shareUrl": TUNNEL,
            "joinCode": CODE,
            "inviteUrl": f"https://abc-def.trycloudflare.com/#code={CODE}",
        }

    def test_still_hands_out_the_code_when_the_link_is_not_ready(self):
        # The gate is live from the moment the board starts — only the address
        # takes a few seconds. Withholding the code too would be a worse lie.
        body = invite_payload({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", CODE)
        assert body == {"shareUrl": "", "joinCode": CODE, "inviteUrl": ""}

    def test_never_the_host_link(self):
        # Every participant can read this response; the host link carries the
        # admin secret. Nothing here may look like one.
        body = invite_payload({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", CODE, TUNNEL)
        assert not any("token=" in value or "admin=" in value for value in body.values())


class TestJoinLimiter:
    def test_blocks_only_after_the_cap(self):
        lim = JoinLimiter()
        for _ in range(JoinLimiter._MAX_FAILS - 1):
            lim.record_failure("10.0.0.1")
        assert lim.blocked("10.0.0.1") is False
        lim.record_failure("10.0.0.1")
        assert lim.blocked("10.0.0.1") is True

    def test_a_success_clears_the_count(self):
        lim = JoinLimiter()
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure("10.0.0.1")
        lim.record_success("10.0.0.1")
        assert lim.blocked("10.0.0.1") is False

    def test_lockout_expires(self):
        clock = {"t": 0.0}
        lim = JoinLimiter(clock=lambda: clock["t"])
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure("10.0.0.1")
        assert lim.blocked("10.0.0.1") is True
        clock["t"] += JoinLimiter._LOCKOUT_S + 1
        assert lim.blocked("10.0.0.1") is False


class _FakeHandler:
    """A handler stand-in with headers and a socket address, and nothing else."""

    def __init__(self, headers: dict[str, str] | None = None, peer: str = "127.0.0.1"):
        self.headers = headers or {}
        self.client_address = (peer, 54321)


class TestSecretEqual:
    """Credential comparison must be total — a bad credential is a 403, not a crash."""

    def test_matching_ascii(self):
        assert access.secret_equal("ABCD-1234", "ABCD-1234")

    def test_mismatched_ascii(self):
        assert not access.secret_equal("ABCD-1234", "WXYZ-9999")

    def test_non_ascii_compares_false_instead_of_raising(self):
        # secrets.compare_digest raises TypeError on a non-ASCII str, and every
        # credential here arrives from the network. Unguarded, `?token=café`
        # printed a traceback over the live TUI and dropped the connection —
        # and on the join path it skipped the lockout counter entirely.
        assert not access.secret_equal("café", "ABCD-1234")
        assert not access.secret_equal("ABCD-1234", "café")
        assert not access.secret_equal("café", "café's twin")

    def test_identical_non_ascii_still_matches(self):
        # Not a case that can arise from a generated credential, but the
        # function must stay a comparison rather than a blanket refusal.
        assert access.secret_equal("café", "café")

    def test_empty_supplied_never_matches_a_real_secret(self):
        assert not access.secret_equal("", "ABCD-1234")


class TestClientKey:
    """Behind the tunnel, client_address is 127.0.0.1 for every remote visitor."""

    def test_forwarded_header_wins_when_trusted(self):
        h = _FakeHandler({"CF-Connecting-IP": "203.0.113.7"})
        assert access.client_key(h, trust_forwarded=True) == "203.0.113.7"

    def test_forwarded_header_ignored_when_not_trusted(self):
        # No tunnel is up, so the header is just a string the client chose.
        h = _FakeHandler({"CF-Connecting-IP": "203.0.113.7"})
        assert access.client_key(h, trust_forwarded=False) == "127.0.0.1"

    def test_two_visitors_through_one_tunnel_get_distinct_keys(self):
        # The whole point: without this both were "127.0.0.1", so eight wrong
        # codes from one of them locked the other out.
        a = _FakeHandler({"CF-Connecting-IP": "203.0.113.7"})
        b = _FakeHandler({"CF-Connecting-IP": "198.51.100.4"})
        assert access.client_key(a, trust_forwarded=True) != access.client_key(b, trust_forwarded=True)

    def test_x_forwarded_for_takes_the_leftmost_entry(self):
        h = _FakeHandler({"X-Forwarded-For": "203.0.113.7, 70.41.3.18"})
        assert access.client_key(h, trust_forwarded=True) == "203.0.113.7"

    def test_cf_header_preferred_over_x_forwarded_for(self):
        h = _FakeHandler({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.4"})
        assert access.client_key(h, trust_forwarded=True) == "203.0.113.7"

    def test_junk_header_falls_back_rather_than_keying_on_it(self):
        # A header is attacker-controlled text; it must never become a bucket key.
        h = _FakeHandler({"CF-Connecting-IP": "not-an-address"})
        assert access.client_key(h, trust_forwarded=True) == "127.0.0.1"

    def test_absurdly_long_header_falls_back(self):
        h = _FakeHandler({"CF-Connecting-IP": "1" * 5000})
        assert access.client_key(h, trust_forwarded=True) == "127.0.0.1"

    def test_missing_header_falls_back(self):
        assert access.client_key(_FakeHandler(), trust_forwarded=True) == "127.0.0.1"

    def test_ipv6_is_accepted_and_normalised(self):
        h = _FakeHandler({"CF-Connecting-IP": "2001:DB8::1"})
        assert access.client_key(h, trust_forwarded=True) == "2001:db8::1"


class TestLimiterIsBounded:
    def test_table_does_not_grow_without_limit(self):
        # Keys now come from a forwarded header, so how many distinct ones exist
        # is chosen by whoever is talking to us.
        limiter = access.JoinLimiter()
        for i in range(access.JoinLimiter._MAX_TRACKED * 2):
            limiter.record_failure(f"198.51.100.{i}")
        assert len(limiter._fails) <= access.JoinLimiter._MAX_TRACKED

    def test_a_real_lockout_survives_eviction_pressure(self):
        limiter = access.JoinLimiter()
        for _ in range(access.JoinLimiter._MAX_FAILS):
            limiter.record_failure("203.0.113.7")
        assert limiter.blocked("203.0.113.7")
        # Flooding the table with spoofed addresses must not flush the lockout.
        # Plain oldest-first eviction would: the attacker's entry is among the
        # oldest, because they failed before they started flooding.
        for i in range(access.JoinLimiter._MAX_TRACKED * 2):
            limiter.record_failure(f"198.51.{i // 255}.{i % 255}")
        assert limiter.blocked("203.0.113.7"), "a flood must not evict an active lockout"
