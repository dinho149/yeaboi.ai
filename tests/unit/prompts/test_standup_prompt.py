"""Unit tests for the Daily Standup summary prompt."""

from yeaboi.prompts.standup import get_standup_summary_prompt


def _prompt(**over) -> str:
    base = dict(
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_label="At risk",
        confidence_rationale="behind ideal burn",
        members=[
            {
                "name": "Alice",
                "activity": [{"kind": "commit", "title": "auth pairing", "status": "", "source": "github"}],
                "self_report": "Paired with Bob on auth.",
            },
            {"name": "Bob", "activity": [], "self_report": ""},
        ],
        activity_counts=[("github", 2), ("jira", 1)],
    )
    base.update(over)
    return get_standup_summary_prompt(**base)


class TestStandupSummaryPrompt:
    def test_contains_sprint_context(self):
        p = _prompt()
        assert "Sprint 5" in p
        assert "day 3 of 10" in p
        assert "At risk" in p
        assert "github: 2, jira: 1" in p

    def test_members_include_activity_and_self_report(self):
        p = _prompt()
        assert "auth pairing" in p
        assert "Paired with Bob on auth." in p

    def test_self_report_is_supporting_context_not_replacement(self):
        # The grounding requirement: analysis still runs for self-reporters.
        p = _prompt()
        assert "supporting context" in p
        assert "still describe what their activity shows" in p
        assert "Do NOT simply repeat" in p

    def test_json_shape_requested(self):
        p = _prompt()
        assert '"members"' in p
        assert '"team_summary"' in p

    def test_member_summary_is_terse_clauses_with_keys_only(self):
        # The member summary renders as intro bullets, one per clause — the
        # prompt must demand short semicolon-separated clauses and ban
        # restating ticket titles (they render as evidence rows, and the
        # exporter appends the title to each first key mention itself).
        p = _prompt()
        assert "2-4 TERSE CLAUSES separated by semicolons" in p
        assert "at most 10 words" in p
        assert "refer to tickets by key only" in p
        assert "NEVER restate a ticket's title" in p

    def test_continuing_is_reserved_for_in_progress(self):
        # A rank drag or field edit must not be narrated as work the person is
        # "continuing" — that word belongs to their assigned in-progress list.
        p = _prompt()
        assert "RESERVED for 'in_progress' items" in p
        assert "never as work the person is continuing or carrying" in p

    def test_empty_counts(self):
        p = _prompt(activity_counts=[])
        assert "no activity sources reported" in p


class TestInProgressRules:
    def test_in_progress_payload_rendered(self):
        p = _prompt(
            members=[
                {
                    "name": "Eve",
                    "activity": [],
                    "in_progress": [
                        {"kind": "wip", "title": "Ship exports", "status": "In Progress", "source": "jira"}
                    ],
                    "self_report": "",
                }
            ]
        )
        assert "Ship exports" in p
        assert "in_progress" in p

    def test_continuing_work_rule_present(self):
        p = _prompt()
        assert "Continuing work on" in p
        assert "never say 'No activity detected' for them" in p

    def test_kind_glossary_present(self):
        p = _prompt()
        assert "'comment' (engaged in a discussion)" in p
        assert "'page'/'page-created' (wrote documentation)" in p


class TestDayOverDayPrompt:
    def test_yesterday_payload_rendered(self):
        p = _prompt(
            members=[
                {
                    "name": "Alice",
                    "activity": [],
                    "self_report": "",
                    "yesterday": {"summary": "Shipped the login page", "blockers": "", "outlook": ""},
                    "blocker_signals": [],
                }
            ]
        )
        assert "Shipped the login page" in p
        assert '"yesterday"' in p

    def test_progress_note_and_outlook_requirements(self):
        p = _prompt()
        assert "'progress_note'" in p
        assert "MUST be an empty string" in p
        assert "'outlook'" in p
        assert "Likely to continue" in p

    def test_blocker_signals_must_be_reflected(self):
        p = _prompt(
            members=[
                {"name": "Alice", "activity": [], "self_report": "", "blocker_signals": ["PSOT-9 'Auth' is in Blocked"]}
            ]
        )
        assert "PSOT-9 'Auth' is in Blocked" in p
        assert "never omit or soften" in p

    def test_json_schema_includes_new_fields(self):
        p = _prompt()
        assert '"progress_note": "..."' in p
        assert '"outlook": "..."' in p

    def test_team_summary_names_blocked_members(self):
        assert "name members with blockers explicitly" in _prompt()
