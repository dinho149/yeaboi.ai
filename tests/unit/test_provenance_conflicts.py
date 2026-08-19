"""Tests for src/yeaboi/provenance/conflicts.py — the conflicts vocabulary."""

from yeaboi.provenance.conflicts import (
    Claim,
    Conflict,
    ResolutionStrategy,
    Severity,
    conflict_confidence,
    find_conflict,
    resolve,
    severity_for,
)


def _claims(*pairs: tuple[str, str], **kw) -> list[Claim]:
    return [Claim(value=v, source_document=s, **kw) for v, s in pairs]


class TestDetection:
    def test_two_sources_disagreeing_is_a_conflict(self):
        conflict = find_conflict("YEA-12", "status", _claims(("Done", "jira"), ("In Progress", "github")))
        assert conflict is not None
        assert conflict.conflict_id == "YEA-12:status:value_conflict"
        assert len(conflict.claims) == 2
        assert conflict.recommended_action  # always says what to do next

    def test_agreement_is_not_a_conflict(self):
        assert find_conflict("YEA-12", "status", _claims(("Done", "jira"), ("Done", "github"))) is None

    def test_one_claim_cannot_conflict(self):
        assert find_conflict("YEA-12", "status", _claims(("Done", "jira"))) is None

    def test_absence_of_a_claim_is_not_disagreement(self):
        # "source B says nothing" must never manufacture a conflict — the
        # same invariant relatedness holds for practice signals.
        claims = _claims(("Done", "jira"), ("", "github"), ("  ", "azdo"))
        assert find_conflict("YEA-12", "status", claims) is None

    def test_explicit_action_wins_over_the_default(self):
        conflict = find_conflict(
            "YEA-12",
            "status",
            _claims(("Done", "jira"), ("Open", "github")),
            recommended_action="Reopen the ticket or close the pull request.",
        )
        assert conflict.recommended_action == "Reopen the ticket or close the pull request."


class TestSeverity:
    def test_critical_property_is_critical(self):
        claims = _claims(("Done", "jira"), ("Open", "github"))
        assert severity_for("status", claims, critical_properties=("status",)) is Severity.CRITICAL

    def test_wide_numeric_spread_is_high(self):
        claims = _claims(("100", "jira"), ("5000", "azdo"))
        assert severity_for("points", claims) is Severity.HIGH

    def test_default_is_medium(self):
        claims = _claims(("a", "jira"), ("b", "github"))
        assert severity_for("summary", claims) is Severity.MEDIUM

    def test_severity_serializes_as_a_word(self):
        conflict = find_conflict("e", "p", _claims(("a", "x"), ("b", "y")))
        assert conflict.severity == "medium"  # payload rules: the word, never a colour


class TestConfidence:
    def test_empty_claims_score_zero(self):
        assert conflict_confidence([]) == 0.0

    def test_diversity_raises_confidence_capped_at_one(self):
        low = conflict_confidence(_claims(("a", "x"), ("a", "y"), confidence=0.4))
        high = conflict_confidence(_claims(("a", "x"), ("b", "y"), confidence=0.4))
        assert high > low
        assert conflict_confidence(_claims(("a", "x"), ("b", "y"))) == 1.0


class TestResolution:
    def _conflict(self, *claim_pairs, **claim_kw) -> Conflict:
        return find_conflict("YEA-12", "status", _claims(*claim_pairs, **claim_kw))

    def test_manual_review_is_the_default_and_resolves_nothing(self):
        result = resolve(self._conflict(("Done", "jira"), ("Open", "github")))
        assert result.resolved is False
        assert result.strategy == "manual_review"
        assert ("severity", "medium") in result.extras

    def test_voting_picks_the_majority(self):
        conflict = find_conflict("YEA-12", "status", _claims(("Done", "jira"), ("Done", "slack"), ("Open", "github")))
        result = resolve(conflict, strategy=ResolutionStrategy.VOTING)
        assert result.resolved is True
        assert result.resolved_value == "Done"
        assert result.confidence == round(2 / 3, 4)
        assert set(result.sources_used) == {"jira", "slack"}

    def test_credibility_weighting_can_overturn_a_majority(self):
        conflict = find_conflict("YEA-12", "status", _claims(("Done", "jira"), ("Done", "slack"), ("Open", "github")))
        result = resolve(
            conflict,
            strategy=ResolutionStrategy.CREDIBILITY_WEIGHTED,
            credibility={"github": 0.95, "jira": 0.2, "slack": 0.2},
        )
        assert result.resolved_value == "Open"
        assert 0 < result.confidence < 1

    def test_unknown_source_gets_the_neutral_default(self):
        conflict = self._conflict(("Done", "jira"), ("Open", "mystery"))
        result = resolve(conflict, strategy=ResolutionStrategy.CREDIBILITY_WEIGHTED, credibility={"jira": 0.9})
        assert result.resolved_value == "Done"  # 0.9 beats the 0.5 default

    def test_most_recent_needs_timestamps(self):
        undated = resolve(self._conflict(("Done", "jira"), ("Open", "github")), strategy=ResolutionStrategy.MOST_RECENT)
        assert undated.resolved is False
        assert "timestamp" in undated.notes

    def test_most_recent_picks_the_newest_claim(self):
        claims = [
            Claim(value="Done", source_document="jira", observed_at="2026-08-10T00:00:00+00:00"),
            Claim(value="Open", source_document="github", observed_at="2026-08-15T00:00:00+00:00"),
        ]
        conflict = find_conflict("YEA-12", "status", claims)
        result = resolve(conflict, strategy=ResolutionStrategy.MOST_RECENT)
        assert result.resolved_value == "Open"
        assert result.sources_used == ("github",)

    def test_highest_confidence_and_first_seen(self):
        claims = [
            Claim(value="Done", source_document="jira", confidence=0.6),
            Claim(value="Open", source_document="github", confidence=0.9),
        ]
        conflict = find_conflict("YEA-12", "status", claims)
        assert resolve(conflict, strategy=ResolutionStrategy.HIGHEST_CONFIDENCE).resolved_value == "Open"
        assert resolve(conflict, strategy=ResolutionStrategy.FIRST_SEEN).resolved_value == "Done"
