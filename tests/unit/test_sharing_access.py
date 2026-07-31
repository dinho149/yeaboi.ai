"""Unit tests for the shared access credentials and the participant-URL rule.

The interesting one is :func:`participant_url`. Both live boards bind loopback
and are shared only through a Cloudflare tunnel, so "which address do we hand
out" has exactly one right answer and two wrong ones — the host's own
``127.0.0.1``, and nothing at all when a link genuinely exists.
"""

import pytest

from yeaboi.sharing.access import (
    JoinLimiter,
    invite_payload,
    make_join_code,
    make_token,
    participant_url,
)

TUNNEL = "https://abc-def.trycloudflare.com/"


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


class TestInvitePayload:
    def test_carries_the_code_and_the_public_url(self):
        body = invite_payload({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", "K3P9-2QXA", TUNNEL)
        assert body == {"shareUrl": TUNNEL, "joinCode": "K3P9-2QXA"}

    def test_still_hands_out_the_code_when_the_link_is_not_ready(self):
        # The gate is live from the moment the board starts — only the address
        # takes a few seconds. Withholding the code too would be a worse lie.
        body = invite_payload({"Host": "127.0.0.1:5173"}, "127.0.0.1:5173", "K3P9-2QXA")
        assert body == {"shareUrl": "", "joinCode": "K3P9-2QXA"}


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
