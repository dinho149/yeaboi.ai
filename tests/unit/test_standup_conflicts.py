"""Tests for src/yeaboi/standup/conflicts.py — cross-source conflict cards."""

from yeaboi.standup import conflicts

PREFIXES = frozenset({"YEA"})
NO_IDS = frozenset()


def _item(kind, **fields):
    base = {"kind": kind, "key": "", "status": "", "title": "", "url": "", "source": "", "timestamp": ""}
    base.update(fields)
    return base


def _detect(grouped, *, prefixes=PREFIXES, work_item_ids=NO_IDS):
    return conflicts.detect_status_conflicts(grouped, prefixes=prefixes, work_item_ids=work_item_ids)


class TestDetection:
    def test_done_ticket_with_open_pr_is_a_card(self):
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="Done", source="jira", url="https://j/YEA-12"),
                _item("pr", status="open", title="YEA-12 finish the wiring", source="github", url="https://g/41"),
            ]
        }
        cards, warnings = _detect(grouped)
        assert warnings == ()
        assert len(cards) == 1
        card = cards[0]
        assert card.entity_id == "YEA-12"
        assert card.property_name == "status"
        assert card.severity == "medium"
        assert card.members == ("alice",)
        assert "Done" in card.title
        assert card.recommended_action
        # Both claims, each with its source and evidence url.
        sources = {claim[0] for claim in card.claims}
        assert sources == {"jira", "github"}
        assert {claim[3] for claim in card.claims} == {"https://j/YEA-12", "https://g/41"}

    def test_merged_pr_is_settled_not_a_conflict(self):
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="Done", source="jira"),
                _item("pr", status="merged", title="YEA-12 finish", source="github"),
            ]
        }
        cards, _ = _detect(grouped)
        assert cards == ()

    def test_open_ticket_is_not_a_conflict(self):
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="In Progress", source="jira"),
                _item("pr", status="open", title="YEA-12 finish", source="github"),
            ]
        }
        cards, _ = _detect(grouped)
        assert cards == ()

    def test_newest_ticket_observation_wins(self):
        # Done at 09:00, reopened at 11:00 — a stale done-sighting must never
        # survive a later contradicting one.
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="Done", timestamp="2026-08-16T09:00:00Z", source="jira"),
                _item("issue", key="YEA-12", status="In Progress", timestamp="2026-08-16T11:00:00Z", source="jira"),
                _item("pr", status="open", title="YEA-12 finish", source="github"),
            ]
        }
        cards, _ = _detect(grouped)
        assert cards == ()

    def test_unrelated_open_pr_is_not_a_conflict(self):
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="Done", source="jira"),
                _item("pr", status="open", title="refactor the parser", source="github"),
            ]
        }
        cards, _ = _detect(grouped)
        assert cards == ()

    def test_azdo_linked_work_item_counts_as_a_reference(self):
        grouped = {
            "bob": [
                _item("work_item", key="123", status="Closed", source="azdo"),
                _item("pr", status="active", title="finish the exporter", source="azdo", work_item_ids=["123"]),
            ]
        }
        cards, _ = _detect(grouped, prefixes=frozenset(), work_item_ids=frozenset({"123"}))
        assert len(cards) == 1
        assert cards[0].entity_id == "123"

    def test_cross_member_conflict_names_the_pr_holder(self):
        # Alice's ticket is done; Bob still holds the open PR that names it.
        grouped = {
            "alice": [_item("issue", key="YEA-12", status="Done", source="jira")],
            "bob": [_item("pr", status="open", title="YEA-12 finish", source="github")],
        }
        cards, _ = _detect(grouped)
        assert cards[0].members == ("bob",)

    def test_second_open_pr_is_counted_not_dropped(self):
        grouped = {
            "alice": [
                _item("issue", key="YEA-12", status="Done", source="jira"),
                _item("pr", status="open", title="YEA-12 part one", source="github"),
                _item("pr", status="open", title="YEA-12 part two", source="github"),
            ]
        }
        cards, _ = _detect(grouped)
        assert len(cards) == 1
        assert "+1 more" in cards[0].detail

    def test_quiet_day_produces_nothing(self):
        cards, warnings = _detect({"alice": []})
        assert cards == ()
        assert warnings == ()


class TestCap:
    def test_cards_cap_with_a_warning_naming_the_remainder(self):
        grouped = {
            "alice": [
                item
                for n in range(12)
                for item in (
                    _item("issue", key=f"YEA-{n}", status="Done", source="jira"),
                    _item("pr", status="open", title=f"YEA-{n} work", source="github"),
                )
            ]
        }
        cards, warnings = _detect(grouped)
        assert len(cards) == conflicts._MAX_CARDS
        assert len(warnings) == 1
        assert "4 more" in warnings[0]
