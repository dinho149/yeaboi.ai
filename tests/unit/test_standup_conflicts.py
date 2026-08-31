"""Tests for src/yeaboi/standup/conflicts.py — cross-source conflict cards."""

from yeaboi.agent.state import ConflictCard
from yeaboi.ops.events import OpsEvent
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


def _event(**fields):
    base = {
        "kind": "incident",
        "source": "pagerduty",
        "ref": "PD-1",
        "title": "",
        "status": "triggered",
    }
    base.update(fields)
    return OpsEvent(**base)


class TestProductionDetection:
    def test_done_ticket_with_a_live_incident_naming_it_is_a_card(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        cards = conflicts.detect_ops_conflicts(
            grouped, [_event(title="YEA-9 checkout is down", service="checkout")], prefixes=PREFIXES
        )
        assert len(cards) == 1
        card = cards[0]
        assert card.entity_id == "YEA-9"
        assert "still triggered" in card.title
        assert "checkout" in card.detail
        assert card.fingerprint.endswith(":ops_conflict")

    def test_nobody_is_named(self):
        # Attributing an incident to whoever merged the PR would reintroduce
        # blame by the back door — an alert firing is nobody's work.
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        cards = conflicts.detect_ops_conflicts(grouped, [_event(title="YEA-9 down")], prefixes=PREFIXES)
        assert cards[0].members == ()

    def test_a_resolved_incident_is_agreement_not_a_conflict(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        assert (
            conflicts.detect_ops_conflicts(grouped, [_event(title="YEA-9 down", status="resolved")], prefixes=PREFIXES)
            == ()
        )

    def test_an_event_with_no_status_asserts_nothing(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        assert conflicts.detect_ops_conflicts(grouped, [_event(title="YEA-9 down", status="")], prefixes=PREFIXES) == ()

    def test_an_open_ticket_is_not_a_conflict(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="In Progress", source="jira")]}
        assert conflicts.detect_ops_conflicts(grouped, [_event(title="YEA-9 down")], prefixes=PREFIXES) == ()

    def test_the_prefix_gate_applies_to_monitor_names_too(self):
        # "SHA-256 latency" is a monitor name, not a ticket, and OTHER-1 is a
        # prefix no tracker emitted in this run.
        grouped = {"alice": [_item("issue", key="OTHER-1", status="Done", source="jira")]}
        assert conflicts.detect_ops_conflicts(grouped, [_event(title="OTHER-1 down")], prefixes=PREFIXES) == ()

    def test_an_azdo_reference_in_an_alert_cannot_invent_a_work_item(self):
        # AB#42 is ungated on a pull request because the tracker itself spells
        # it that way. A monitor named "AB#42 latency" is a monitor name.
        grouped = {"alice": [_item("issue", key="42", status="Done", source="azure_devops")]}
        assert conflicts.detect_ops_conflicts(grouped, [_event(title="AB#42 latency")], prefixes=PREFIXES) == ()

    def test_a_second_incident_on_the_same_ticket_is_counted_not_dropped(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        cards = conflicts.detect_ops_conflicts(
            grouped,
            [_event(title="YEA-9 down", ref="PD-1"), _event(title="YEA-9 flapping", ref="PD-2")],
            prefixes=PREFIXES,
        )
        assert len(cards) == 1
        assert "(+1 more)" in cards[0].detail

    def test_the_action_names_the_incident_so_it_can_be_settled(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        cards = conflicts.detect_ops_conflicts(grouped, [_event(title="YEA-9 down")], prefixes=PREFIXES)
        assert "PD-1" in cards[0].recommended_action and "pagerduty" in cards[0].recommended_action

    def test_a_quiet_production_produces_nothing(self):
        grouped = {"alice": [_item("issue", key="YEA-9", status="Done", source="jira")]}
        assert conflicts.detect_ops_conflicts(grouped, [], prefixes=PREFIXES) == ()


class TestMerge:
    def test_board_cards_are_never_evicted(self):
        board = tuple(ConflictCard(fingerprint=f"b{n}") for n in range(8))
        ops_cards = tuple(ConflictCard(fingerprint=f"o{n}") for n in range(10))
        merged, warnings = conflicts.merge_cards(board, ops_cards)
        assert merged[:8] == board
        assert len(merged) == 8 + conflicts._MAX_OPS_CARDS
        assert "7 more" in warnings[0]

    def test_neither_list_is_resorted(self):
        board = (ConflictCard(fingerprint="b1"), ConflictCard(fingerprint="b0"))
        ops_cards = (ConflictCard(fingerprint="o1"), ConflictCard(fingerprint="o0"))
        merged, warnings = conflicts.merge_cards(board, ops_cards)
        assert [c.fingerprint for c in merged] == ["b1", "b0", "o1", "o0"]
        assert warnings == ()
