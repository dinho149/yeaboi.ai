"""Unit tests for per-engineer identity resolution.

Attribution is the whole basis of a performance artifact: matching too little
puts someone's work outside their own review, and matching too much puts
someone else's work inside it.
"""

from yeaboi.performance import identity


class TestResolveAliases:
    def test_a_bare_name_is_its_own_alias(self):
        assert "ada lovelace" in identity.resolve_aliases("Ada Lovelace")

    def test_an_empty_name_resolves_to_nothing(self):
        assert identity.resolve_aliases("") == frozenset()
        assert identity.resolve_aliases("   ") == frozenset()

    def test_an_email_seen_on_an_item_becomes_an_alias(self):
        aliases = identity.resolve_aliases(
            "Ada Lovelace",
            items=[{"author": "Ada Lovelace", "author_email": "ada.l@corp.com"}],
        )
        assert "ada.l@corp.com" in aliases
        assert "ada.l" in aliases  # the local part closes the chain too

    def test_extra_handles_are_folded_in(self):
        aliases = identity.resolve_aliases("Ada Lovelace", extra=("alovelace", "ada@corp.com"))
        assert {"alovelace", "ada@corp.com", "ada"} <= aliases

    def test_another_persons_email_is_not_absorbed(self):
        aliases = identity.resolve_aliases(
            "Ada Lovelace",
            items=[{"author": "Bob Jones", "author_email": "bob@corp.com"}],
        )
        assert "bob@corp.com" not in aliases


class TestMatches:
    def test_matching_is_case_and_space_insensitive(self):
        aliases = identity.resolve_aliases("Ada Lovelace")
        assert identity.matches("  ADA LOVELACE ", aliases)

    def test_a_prefix_name_is_never_absorbed(self):
        # The standup's conservative rule, and the reason it exists: a substring
        # match would file Samantha's work under Sam.
        assert not identity.matches("Samantha", identity.resolve_aliases("Sam"))
        assert not identity.matches("Sam", identity.resolve_aliases("Samantha"))

    def test_an_empty_alias_set_matches_nothing(self):
        assert not identity.matches("Ada Lovelace", frozenset())

    def test_an_engineer_matches_under_a_secondary_handle(self):
        aliases = identity.resolve_aliases("Ada Lovelace", extra=("ada@corp.com",))
        assert identity.matches("ada@corp.com", aliases)
        assert identity.matches("ada", aliases)


class TestRosterHandles:
    def test_a_broken_roster_costs_nothing(self, monkeypatch):
        def _boom(**_kw):
            raise RuntimeError("no credentials")

        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", _boom)
        assert identity.roster_handles("Ada Lovelace") == ()

    def test_the_matching_members_identity_and_email_are_returned(self, monkeypatch):
        from yeaboi.agent.state import EngineerRef

        monkeypatch.setattr(
            "yeaboi.performance.roster.fetch_roster",
            lambda **_kw: [
                EngineerRef(name="Bob Jones", email="bob@corp.com"),
                EngineerRef(name="Ada Lovelace", external_id="acct:123", email="ada@corp.com"),
            ],
        )
        assert identity.roster_handles("Ada Lovelace") == ("acct:123", "ada@corp.com")

    def test_an_unknown_engineer_yields_nothing(self, monkeypatch):
        from yeaboi.agent.state import EngineerRef

        monkeypatch.setattr(
            "yeaboi.performance.roster.fetch_roster",
            lambda **_kw: [EngineerRef(name="Bob Jones", email="bob@corp.com")],
        )
        assert identity.roster_handles("Ada Lovelace") == ()
