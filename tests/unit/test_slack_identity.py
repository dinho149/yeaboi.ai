"""The one mapping in the Slack lane a human curates.

Two properties carry this module, and both are negative. **It never gates an
act** — every inbound act works with the table empty, which is what stops leg 2
doing nothing until somebody runs a setup command. And **a name is never
invented**: an unbound id is attributed as ``@U…``, the weaker true statement,
rather than as a guess wearing a person's name.
"""

from __future__ import annotations

import pytest

from yeaboi.slack import identity
from yeaboi.slack.identity import IdentityError
from yeaboi.slack.store import SlackStore


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def roster(monkeypatch):
    """A session whose roster is Ada and Ben, without a standup store behind it."""
    names = ["Ada Lovelace", "Ben Carter"]
    monkeypatch.setattr(identity, "roster", lambda _session: list(names))
    return names


class TestLinking:
    def test_a_link_round_trips(self, db, roster):
        assert identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db) == "U0123456789 → Ada Lovelace"
        assert identity.resolve("s1", "U0123456789", db_path=db) == "Ada Lovelace"

    def test_an_id_is_normalised_on_both_sides(self, db, roster):
        # Slack is consistent about case, but a human typing one into a terminal
        # is not — and a binding that silently did not match is indistinguishable
        # from one nobody made.
        identity.link("s1", "@u0123456789", "Ada Lovelace", db_path=db)
        assert identity.resolve("s1", "U0123456789", db_path=db) == "Ada Lovelace"

    def test_relinking_replaces_rather_than_duplicating(self, db, roster):
        identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db)
        identity.link("s1", "U0123456789", "Ben Carter", db_path=db)
        assert identity.resolve("s1", "U0123456789", db_path=db) == "Ben Carter"
        assert len(identity.listing("s1", db_path=db)) == 1

    def test_bindings_are_per_session(self, db, roster):
        identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db)
        assert identity.resolve("s2", "U0123456789", db_path=db) == ""

    def test_unlinking_says_whether_there_was_anything_to_unlink(self, db, roster):
        identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db)
        assert identity.unlink("s1", "U0123456789", db_path=db) is True
        assert identity.unlink("s1", "U0123456789", db_path=db) is False
        assert identity.resolve("s1", "U0123456789", db_path=db) == ""


class TestWhatIsRefused:
    def test_a_name_off_the_roster_is_refused(self, db, roster):
        # Validated once, on write, where the person who typed it is standing —
        # the `edits.validate` gesture. This string ends up on a teammate's
        # report, so "close enough" is not a state it may be in.
        with pytest.raises(IdentityError, match="not on this session's roster"):
            identity.link("s1", "U0123456789", "Adam Lovelace", db_path=db)
        assert identity.listing("s1", db_path=db) == []

    @pytest.mark.parametrize("bad", ["ada", "U123", "", "UXXXXXXXX"])
    def test_something_that_is_not_a_member_id_is_refused(self, db, roster, bad):
        with pytest.raises(IdentityError, match="not a Slack member id"):
            identity.link("s1", bad, "Ada Lovelace", db_path=db)

    def test_a_session_with_no_roster_is_refused_and_says_the_lane_still_works(self, db, monkeypatch):
        monkeypatch.setattr(identity, "roster", lambda _session: [])
        with pytest.raises(IdentityError, match="still works"):
            identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db)

    def test_no_session_is_refused(self, db, roster):
        with pytest.raises(IdentityError, match="no session"):
            identity.link("", "U0123456789", "Ada Lovelace", db_path=db)


class TestResolutionNeverRaises:
    def test_an_unbound_id_resolves_to_nothing_rather_than_a_guess(self, db):
        assert identity.resolve("s1", "U0123456789", db_path=db) == ""

    def test_an_unreadable_store_resolves_to_nothing(self, tmp_path, monkeypatch):
        def _boom(*_a, **_kw):
            raise OSError("disk gone")

        monkeypatch.setattr("yeaboi.slack.identity.SlackStore", _boom)
        assert identity.resolve("s1", "U0123456789") == ""

    @pytest.mark.parametrize("session,user", [("", "U0123456789"), ("s1", "")])
    def test_a_missing_half_resolves_to_nothing_without_touching_the_store(self, session, user, monkeypatch):
        monkeypatch.setattr("yeaboi.slack.identity.SlackStore", lambda *_a, **_kw: pytest.fail("opened the store"))
        assert identity.resolve(session, user) == ""


class TestSuggestions:
    def test_a_first_name_resolves_when_it_is_unique(self, roster):
        assert identity.suggest("Ada", roster) == "Ada Lovelace"

    def test_an_ambiguous_first_name_yields_nothing(self):
        # `resolve_speakers`' rule, and the reason for it: a mis-bound id puts
        # the wrong person's name on somebody else's correction.
        assert identity.suggest("Ada", ["Ada Lovelace", "Ada Byron"]) == ""

    def test_an_unknown_name_yields_nothing(self, roster):
        assert identity.suggest("Grace Hopper", roster) == ""

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_nothing_in_yields_nothing_out(self, raw, roster):
        assert identity.suggest(raw, roster) == ""


class TestTheTableIsConfigurationNotTelemetry:
    def test_prune_never_drops_an_identity(self, db, roster):
        # The other three tables have a shelf life; this one is something a
        # human typed, and a poll that quietly forgot it a month later would
        # start attributing their corrections to a raw id with no explanation.
        identity.link("s1", "U0123456789", "Ada Lovelace", db_path=db)
        with SlackStore(db) as store:
            store.prune(keep_days=0)
        assert identity.resolve("s1", "U0123456789", db_path=db) == "Ada Lovelace"
