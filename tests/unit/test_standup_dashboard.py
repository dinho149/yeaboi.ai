"""The standup dashboard's card vocabulary — shared by the TUI and the desktop."""

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.standup import dashboard


def _report(**kw) -> StandupReport:
    defaults = {
        "date": "2026-07-10",
        "team_summary": "steady progress",
        "member_updates": (
            MemberUpdate(name="Ana", summary="login page", activity_count=3),
            MemberUpdate(name="Bo", summary="No activity detected.", activity_count=0),
        ),
        "activity_counts": (("github", 2),),
    }
    return StandupReport(**{**defaults, **kw})


class TestCardOrder:
    def test_only_schedule_before_a_report_exists(self):
        assert dashboard.card_order({"report": None}) == ["schedule"]

    def test_the_standard_run(self):
        assert dashboard.card_order({"report": _report()}) == [
            "summary",
            "my_update",
            "team",
            "activity",
            "schedule",
        ]

    def test_notices_only_when_the_report_warns(self):
        assert "notices" not in dashboard.card_order({"report": _report()})
        assert "notices" in dashboard.card_order({"report": _report(warnings=("Jira: auth failed",))})

    def test_expanding_the_team_inlines_a_row_per_other_member(self):
        data = {"report": _report(), "my_name": "Ana", "team_expanded": True}
        assert dashboard.card_order(data) == [
            "summary",
            "my_update",
            "team",
            "member:Bo",
            "activity",
            "schedule",
        ]

    def test_a_nudge_earns_the_transcript_card_with_no_review(self):
        # An unchecked-standups count IS a result, so it earns the card on the
        # same terms an actual review does.
        data = {"report": _report(), "review": None, "nudge": object()}
        assert "gaps" in dashboard.card_order(data)

    def test_no_transcript_card_without_a_review_or_a_nudge(self):
        assert "gaps" not in dashboard.card_order({"report": _report(), "nudge": None})


class TestTitles:
    def test_every_card_key_has_a_title(self):
        data = {"report": _report(warnings=("x",)), "review": object()}
        for key in dashboard.card_order(data):
            assert dashboard.CARD_TITLES[key]

    def test_a_member_row_is_titled_by_the_member(self):
        assert dashboard.card_title("member:Ana") == "Ana"

    def test_cards_carry_key_title_and_member(self):
        data = {"report": _report(), "my_name": "Ana", "team_expanded": True}
        member_card = next(c for c in dashboard.cards(data) if c["key"] == "member:Bo")
        assert member_card == {"key": "member:Bo", "title": "Bo", "member": "Bo"}
        assert all(c["member"] == "" for c in dashboard.cards(data) if not c["key"].startswith("member:"))


class TestMemberActive:
    def test_counted_activity_is_active(self):
        assert dashboard.member_active(MemberUpdate(name="Ana", summary="", activity_count=2))

    def test_the_no_activity_summary_is_quiet(self):
        assert not dashboard.member_active(MemberUpdate(name="Bo", summary="No activity detected."))

    def test_an_old_report_falls_back_to_the_summary(self):
        # Reports saved before activity_count existed deserialize with 0 for
        # everyone — the whole team must not render as quiet.
        assert dashboard.member_active(MemberUpdate(name="Cy", summary="shipped the exporter", activity_count=0))


class TestOtherMembers:
    def test_the_standup_user_is_excluded(self):
        names = [m.name for m in dashboard.other_members({"report": _report(), "my_name": "Ana"})]
        assert names == ["Bo"]

    def test_no_report_means_no_members(self):
        assert dashboard.other_members({"report": None}) == []


class TestTuiParity:
    def test_the_tui_page_reads_this_vocabulary(self):
        from yeaboi.ui.mode_select.screens import _standup_sections as sections

        assert sections.standup_card_order is dashboard.card_order
        data = {"report": _report(warnings=("x",))}
        for key in dashboard.card_order(data):
            # The terminal may decorate a title with a glyph, but never rename it.
            assert dashboard.CARD_TITLES[key] in sections.standup_card_title(key, data)
