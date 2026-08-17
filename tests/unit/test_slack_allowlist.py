"""Who may act from Slack. Every test here is about failing closed.

This is the first attested identity anything in yeaboi has had, and the failure
that matters is not "somebody unauthorised got in" — it is "the list looked
configured and was not".
"""

from __future__ import annotations

import pytest

from yeaboi.slack.allowlist import AllowlistError, authorised, describe, is_placeholder, load, parse


class TestParse:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("U0123456789", ("U0123456789",)),
            ("U0123456789,U9876543210", ("U0123456789", "U9876543210")),
            ("U0123456789, U9876543210", ("U0123456789", "U9876543210")),
            ("U0123456789 U9876543210", ("U0123456789", "U9876543210")),
            ("  U0123456789  ", ("U0123456789",)),
            # Enterprise Grid ids start with W.
            ("W0123456789", ("W0123456789",)),
            ("u0123456789", ("U0123456789",)),
        ],
    )
    def test_accepts_real_member_ids(self, raw, expected):
        assert parse(raw) == expected

    def test_dedupes_but_keeps_the_order_somebody_wrote(self):
        assert parse("U9876543210,U0123456789,U9876543210") == ("U9876543210", "U0123456789")

    @pytest.mark.parametrize(
        "raw",
        ["ada", "@ada", "U123", "C0123456789", "U0123456789 ada", "<@U0123456789>", "ada@example.com"],
    )
    def test_one_malformed_entry_voids_the_whole_list(self, raw):
        # The relay's hardest-won rule. A half-filled allowlist is the more
        # dangerous state because it LOOKS configured, and a typo must not
        # silently reduce it to the ids that happened to parse.
        with pytest.raises(AllowlistError):
            parse(raw)

    def test_the_error_names_the_offending_entry(self):
        with pytest.raises(AllowlistError, match="'ada'"):
            parse("U0123456789,ada")

    @pytest.mark.parametrize("raw", ["UXXXXXXXX", "U000000000", "U123456789"])
    def test_an_unedited_example_is_refused(self, raw):
        assert is_placeholder(raw)
        with pytest.raises(AllowlistError):
            parse(raw)

    @pytest.mark.parametrize("raw", ["", "   ", ","])
    def test_empty_parses_to_nobody_rather_than_raising(self, raw):
        assert parse(raw) == ()


class TestLoad:
    def test_reads_the_configured_list(self, monkeypatch):
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "U0123456789")
        assert load() == ("U0123456789",)

    def test_unset_is_nobody(self, monkeypatch):
        monkeypatch.delenv("SLACK_ALLOWED_MEMBER_IDS", raising=False)
        assert load() == ()

    def test_an_unusable_list_is_nobody_and_never_raises(self, monkeypatch):
        # The caller is an unattended poll. "Nobody is authorised" is the safe
        # reading of every failure here.
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "U0123456789,oops")
        assert load() == ()


class TestAuthorised:
    ALLOWED = ("U0123456789", "U9876543210")

    def test_a_listed_member_may_act(self):
        assert authorised("U0123456789", self.ALLOWED)

    def test_case_does_not_matter(self):
        assert authorised("u0123456789", self.ALLOWED)

    def test_an_unlisted_member_may_not(self):
        assert not authorised("U5555555555", self.ALLOWED)

    def test_nobody_may_act_against_an_empty_list(self):
        assert not authorised("U0123456789", ())

    def test_an_empty_actor_may_not(self):
        assert not authorised("", self.ALLOWED)

    def test_the_bot_is_never_authorised_even_if_listed(self):
        # Otherwise our own acknowledgement reaction authorises the next round,
        # and the lane drives itself.
        assert not authorised("U0123456789", self.ALLOWED, bot_user_id="U0123456789")

    def test_the_bot_exclusion_is_case_insensitive(self):
        assert not authorised("u0123456789", self.ALLOWED, bot_user_id="U0123456789")


class TestDescribe:
    def test_says_plainly_when_nobody_can_act(self, monkeypatch):
        monkeypatch.delenv("SLACK_ALLOWED_MEMBER_IDS", raising=False)
        assert "nobody" in describe()

    def test_reports_the_members(self, monkeypatch):
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "U0123456789,U9876543210")
        assert "2 member(s)" in describe()

    def test_a_broken_list_explains_itself(self, monkeypatch):
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "nope")
        assert "nobody is authorised" in describe()
