"""Unit tests for standup insights: blocker signals + yesterday context."""

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.standup import insights
from yeaboi.standup.insights import detect_blocker_signals, yesterday_context


def _prev_report(**member_kwargs) -> StandupReport:
    return StandupReport(
        date="2026-07-24",
        session_id="s1",
        member_updates=(MemberUpdate(name="Alice", **member_kwargs),),
    )


class TestBlockedStatus:
    def test_blocked_issue_fires(self):
        grouped = {"Alice": [{"kind": "issue", "key": "PSOT-9", "title": "Auth flow", "status": "Blocked"}]}
        signals = detect_blocker_signals(grouped)
        assert signals["Alice"] == ("PSOT-9 'Auth flow' is in Blocked",)

    def test_wip_and_work_item_and_update_kinds_fire(self):
        for kind in ("wip", "work_item", "update"):
            grouped = {"Alice": [{"kind": kind, "key": "AB-1", "title": "t", "status": "On Hold"}]}
            assert "Alice" in detect_blocker_signals(grouped), kind

    def test_waiting_for_prefix_fires(self):
        grouped = {"Alice": [{"kind": "issue", "key": "AB-1", "title": "t", "status": "Waiting for deploy window"}]}
        assert "Alice" in detect_blocker_signals(grouped)

    def test_normal_statuses_ignored(self):
        for status in ("In Progress", "Done", "In Review", "To Do", "Waiting Room Feature", ""):
            grouped = {"Alice": [{"kind": "issue", "key": "AB-1", "title": "t", "status": status}]}
            assert detect_blocker_signals(grouped) == {}, status

    def test_non_status_kinds_ignored(self):
        # A commit titled "fix blocked pipeline" must never read as a blocker.
        grouped = {"Alice": [{"kind": "commit", "key": "", "title": "fix blocked pipeline", "status": "blocked"}]}
        assert detect_blocker_signals(grouped) == {}

    def test_same_ticket_deduped_across_kinds(self):
        grouped = {
            "Alice": [
                {"kind": "issue", "key": "AB-1", "title": "t", "status": "Blocked"},
                {"kind": "update", "key": "AB-1", "title": "moved AB-1 't' to Blocked", "status": "Blocked"},
            ]
        }
        assert len(detect_blocker_signals(grouped)["Alice"]) == 1

    def test_signal_capped_per_member(self):
        grouped = {"Alice": [{"kind": "issue", "key": f"AB-{i}", "title": "t", "status": "Blocked"} for i in range(6)]}
        assert len(detect_blocker_signals(grouped)["Alice"]) == insights._MAX_SIGNALS_PER_MEMBER


class TestPrOpenAcrossStandups:
    def test_pr_seen_yesterday_still_open_fires(self):
        prev = _prev_report(code_links=(("export refactor", "https://g.h/acme/app/pull/490"),))
        grouped = {
            "Alice": [
                {
                    "kind": "pr",
                    "key": "#490",
                    "title": "export refactor",
                    "status": "open",
                    "url": "https://g.h/acme/app/pull/490",
                }
            ]
        }
        signals = detect_blocker_signals(grouped, previous_report=prev)
        assert signals["Alice"] == ("PR #490 'export refactor' still open since the last standup",)

    def test_merged_pr_no_signal(self):
        prev = _prev_report(code_links=(("x", "https://g.h/p/1"),))
        grouped = {"Alice": [{"kind": "pr", "key": "#1", "title": "x", "status": "merged", "url": "https://g.h/p/1"}]}
        assert detect_blocker_signals(grouped, previous_report=prev) == {}

    def test_new_pr_not_in_previous_report_no_signal(self):
        prev = _prev_report(code_links=(("other", "https://g.h/p/2"),))
        grouped = {"Alice": [{"kind": "pr", "key": "#1", "title": "x", "status": "open", "url": "https://g.h/p/1"}]}
        assert detect_blocker_signals(grouped, previous_report=prev) == {}

    def test_no_previous_report_rule_off(self):
        grouped = {"Alice": [{"kind": "pr", "key": "#1", "title": "x", "status": "open", "url": "https://g.h/p/1"}]}
        assert detect_blocker_signals(grouped, previous_report=None) == {}

    def test_legacy_links_field_counts(self):
        prev = _prev_report(links=(("x", "https://g.h/p/1"),))
        grouped = {"Alice": [{"kind": "pr", "key": "#1", "title": "x", "status": "open", "url": "https://g.h/p/1"}]}
        assert "Alice" in detect_blocker_signals(grouped, previous_report=prev)


class TestCommentChurn:
    def _churn_grouped(self, n_comments=4, commenters=("Bob", "Carla")):
        grouped: dict = {"Alice": [{"kind": "issue", "key": "AB-7", "title": "t", "status": "In Progress"}]}
        for i in range(n_comments):
            name = commenters[i % len(commenters)]
            grouped.setdefault(name, []).append(
                {"kind": "comment", "key": "AB-7", "title": f"commented on AB-7 ({i})", "status": ""}
            )
        return grouped

    def test_churn_attributed_to_ticket_owner(self):
        signals = detect_blocker_signals(self._churn_grouped())
        assert signals["Alice"] == ("Heavy discussion on AB-7 (4 comments)",)
        assert "Bob" not in signals  # commenters are not flagged, the owner is

    def test_below_comment_floor_no_signal(self):
        assert detect_blocker_signals(self._churn_grouped(n_comments=3)) == {}

    def test_single_commenter_no_signal(self):
        assert detect_blocker_signals(self._churn_grouped(commenters=("Bob",))) == {}

    def test_orphan_key_dropped(self):
        grouped = self._churn_grouped()
        grouped.pop("Alice")  # nobody owns AB-7 anymore
        assert detect_blocker_signals(grouped) == {}


class TestYesterdayContext:
    def test_none_returns_empty(self):
        assert yesterday_context(None) == {}

    def test_maps_summary_blockers_outlook(self):
        prev = _prev_report(summary="Did X", blockers="waiting on review", outlook="Likely to finish X")
        ctx = yesterday_context(prev)
        assert ctx["Alice"] == {"summary": "Did X", "blockers": "waiting on review", "outlook": "Likely to finish X"}

    def test_truncates_long_values(self):
        prev = _prev_report(summary="x" * 500)
        assert len(yesterday_context(prev)["Alice"]["summary"]) <= insights._YESTERDAY_CLIP

    def test_fully_empty_member_omitted(self):
        prev = _prev_report(summary="", blockers="", outlook="")
        assert yesterday_context(prev) == {}


class TestCorrectionsFeedForward:
    """A corrected standup tells the next one more than its text.

    The corrected text already arrives on its own, because a corrected row
    supersedes its parent. What the flag adds is that the team *looked at this
    and disagreed* — which is the part worth not repeating.
    """

    def _edit(self, path):
        from yeaboi.artifacts.edits import Edit

        return Edit(edit_id="e1", op="set", path=path, value="x")

    def test_a_corrected_member_is_flagged(self):
        from yeaboi.agent.state import MemberUpdate, StandupReport
        from yeaboi.standup.insights import yesterday_context

        report = StandupReport(member_updates=(MemberUpdate(name="Ada", summary="Landed login."),))
        out = yesterday_context(report, corrections=(self._edit("member_updates[name=Ada].summary"),))
        assert out["Ada"]["corrected"] == ["summary"]

    def test_an_uncorrected_member_carries_no_flag(self):
        from yeaboi.agent.state import MemberUpdate, StandupReport
        from yeaboi.standup.insights import yesterday_context

        report = StandupReport(
            member_updates=(MemberUpdate(name="Ada", summary="a"), MemberUpdate(name="Grace", summary="b"))
        )
        out = yesterday_context(report, corrections=(self._edit("member_updates[name=Ada].summary"),))
        assert "corrected" not in out["Grace"]

    def test_an_escaped_name_is_read_back_correctly(self):
        from yeaboi.agent.state import MemberUpdate, StandupReport
        from yeaboi.standup.insights import yesterday_context

        report = StandupReport(member_updates=(MemberUpdate(name="Ada Lovelace", summary="a"),))
        out = yesterday_context(report, corrections=(self._edit("member_updates[name=Ada%20Lovelace].summary"),))
        assert out["Ada Lovelace"]["corrected"] == ["summary"]

    def test_a_document_level_correction_flags_nobody(self):
        from yeaboi.agent.state import MemberUpdate, StandupReport
        from yeaboi.standup.insights import yesterday_context

        report = StandupReport(member_updates=(MemberUpdate(name="Ada", summary="a"),))
        out = yesterday_context(report, corrections=(self._edit("team_summary"),))
        assert "corrected" not in out["Ada"]

    def test_an_unparseable_path_is_skipped_not_raised(self):
        # A standup must not fail because a correction from last week was
        # recorded by an older version of the grammar.
        from yeaboi.standup.insights import corrected_members

        assert corrected_members((self._edit("member_updates["),)) == {}

    def test_no_corrections_is_the_shape_it_always_was(self):
        from yeaboi.agent.state import MemberUpdate, StandupReport
        from yeaboi.standup.insights import yesterday_context

        report = StandupReport(member_updates=(MemberUpdate(name="Ada", summary="a"),))
        assert yesterday_context(report) == yesterday_context(report, corrections=())
