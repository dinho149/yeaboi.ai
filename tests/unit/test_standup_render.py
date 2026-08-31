"""Unit tests for standup rendering (plaintext + Rich).

Split out of the old test_standup_delivery.py when the delivery channels were
promoted to ceremonies/: these exercise standup/render.py, which stayed put.
"""

import dataclasses

from rich.console import Group

from yeaboi.agent.state import MemberUpdate, OpsSignal, StandupReport
from yeaboi.standup import render


def _report() -> StandupReport:
    return StandupReport(
        date="2026-07-10",
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_pct=82,
        confidence_label="At risk",
        confidence_rationale="behind ideal burn",
        team_summary="steady progress",
        member_updates=(
            MemberUpdate(name="Alice", summary="login page", source="inferred"),
            MemberUpdate(name="Bob", summary="paired on auth", blockers="waiting on review", source="self-reported"),
        ),
        activity_counts=(("github", 2), ("jira", 1)),
    )


class TestRender:
    def test_plaintext_contains_key_fields(self):
        text = render.format_standup_plaintext(_report())
        assert "Daily Standup — 2026-07-10" in text
        assert "day 3 of 10" in text
        assert "At risk" in text
        assert "Alice" in text
        assert "General overview: login page" in text
        assert "Ticketing:" in text
        assert "Code:" in text
        assert "Documentation:" in text
        assert "Blocker: waiting on review" in text

    def test_rich_returns_group(self):
        assert isinstance(render.format_standup_rich(_report()), Group)

    def test_rich_includes_notices(self):
        from rich.console import Console

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        console = Console(width=90, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(rep))
        out = cap.get()
        assert "Notices" in out
        assert "Jira: authentication failed" in out

    def test_lines_handles_empty_report(self):
        lines = render.format_standup_lines(StandupReport(date="2026-07-10"))
        assert any("No individual updates" in ln for ln in lines)

    def test_warnings_appear_as_notices(self):
        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        text = render.format_standup_plaintext(rep)
        assert "Notices" in text
        assert "Jira: authentication failed" in text

    def test_member_links_render_on_both_surfaces(self):
        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Alice", summary="moved a ticket", links=(("PSOT-1", "https://j/browse/PSOT-1"),)),
            ),
        )
        # Plaintext: raw URL so Slack/email clients auto-link it.
        text = render.format_standup_plaintext(rep)
        assert "🔗 PSOT-1: https://j/browse/PSOT-1" in text
        # Rich: label rendered (OSC-8 hyperlink carries the URL invisibly).
        from rich.console import Console

        console = Console(width=90, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(rep))
        assert "↗ PSOT-1" in cap.get()


class TestRenderDenoise:
    """Both text renderers apply the exporters' de-noise pass — the terminal an
    operator watches must not be noisier than the Slack post the run delivers."""

    def _rich_text(self, rep: StandupReport) -> str:
        from rich.console import Console

        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(rep))
        return cap.get()

    def _quiet(self, name: str) -> MemberUpdate:
        return MemberUpdate(
            name=name,
            summary="No activity detected.",
            ticketing_summary="No ticketing activity detected in the selected sources.",
            code_summary="No code activity detected in the selected repositories.",
            documentation_summary="No documentation activity detected in the selected sources.",
        )

    def test_quiet_members_collapse_to_one_line_on_both_surfaces(self):
        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Alice", summary="login page", ticketing_activity_count=1),
                self._quiet("Bo"),
                self._quiet("Cy"),
            ),
        )
        for text in (render.format_standup_plaintext(rep), self._rich_text(rep)):
            assert "No activity detected: Bo, Cy" in text
            assert "• Bo" not in text

    def test_canonical_empty_category_lines_drop_but_failed_survives(self):
        from yeaboi.standup import categories

        failed = categories.empty_summary("ticketing", categories.FAILED)
        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary="login page",
                    ticketing_summary=failed,
                    code_summary="No code activity detected in the selected repositories.",
                    code_activity_count=0,
                ),
            ),
        )
        for text in (render.format_standup_plaintext(rep), self._rich_text(rep)):
            assert "No code activity detected" not in text
            # "we could not look" is per-member news, never dropped.
            assert "unavailable because the selected" in text

    def test_overview_clauses_dedupe_per_known_ticket(self):
        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary="Edited PSOT-9; continuing PSOT-9 in progress",
                    ticketing_links=(("PSOT-9", "https://j/browse/PSOT-9"),),
                    ticketing_activity_count=1,
                ),
            ),
        )
        text = render.format_standup_plaintext(rep)
        assert "General overview: Edited PSOT-9" in text
        assert "continuing" not in text

    def test_team_summary_renders_verbatim(self):
        # The rationale-echo strip is generation-time only: a host-edited
        # sentence overlapping the rationale must survive both renderers.
        rep = StandupReport(
            date="2026-07-10",
            confidence_rationale="Day 2 of 10: 0 of ~3 ideal points burned (0%).",
            team_summary="We are on day 2 of 10 with 0 of ~3 ideal points burned, hence flagging the API work.",
        )
        for text in (render.format_standup_plaintext(rep), self._rich_text(rep)):
            assert "flagging the API work" in text


class TestDayOverDayRender:
    def _report(self):
        return StandupReport(
            date="2026-07-10",
            confidence_pct=74,
            confidence_label="At risk",
            confidence_delta=-8,
            confidence_trend="declining",
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary="login page",
                    progress_note="Still on PSOT-9 from the last standup.",
                    outlook="Likely to finish PSOT-9 today.",
                ),
            ),
        )

    def test_plaintext_progress_and_outlook_lines(self):
        text = render.format_standup_plaintext(self._report())
        assert "↺ Since last standup: Still on PSOT-9 from the last standup." in text
        assert "→ Outlook: Likely to finish PSOT-9 today." in text

    def test_plaintext_confidence_trend_fragment(self):
        assert "▼ 8 vs last" in render.format_standup_plaintext(self._report())

    def test_improving_fragment(self):
        rep = StandupReport(
            date="2026-07-10",
            confidence_pct=90,
            confidence_label="On track",
            confidence_delta=6,
            confidence_trend="improving",
        )
        assert "▲ +6 vs last" in render.format_standup_plaintext(rep)

    def test_steady_or_no_history_no_fragment(self):
        for trend in ("steady", ""):
            rep = StandupReport(
                date="2026-07-10",
                confidence_pct=90,
                confidence_label="On track",
                confidence_delta=1,
                confidence_trend=trend,
            )
            assert "vs last" not in render.format_standup_plaintext(rep)

    def test_empty_fields_render_no_lines(self):
        text = render.format_standup_plaintext(_report())
        assert "Since last standup" not in text
        assert "Outlook" not in text

    def test_rich_includes_progress_and_outlook(self):
        from rich.console import Console

        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(self._report()))
        out = cap.get()
        assert "Since last standup" in out
        assert "Outlook" in out


class TestRenderPractices:
    def _report_with_practices(self, **signal_over):
        from yeaboi.agent.state import PracticeSignal

        base = dict(
            rule="untracked-work",
            title="Untracked work",
            detail="#91 carries no ticket reference in the branch, title, or description.",
            evidence=(("#91", "https://x/pull/91"),),
        )
        base.update(signal_over)
        return StandupReport(
            date="2026-07-10",
            member_updates=(MemberUpdate(name="Alice", summary="login page", practices=(PracticeSignal(**base),)),),
            practice_rollup=(("untracked-work", 2),),
        )

    def test_plaintext_shows_the_signal_after_the_blocker(self):
        text = render.format_standup_plaintext(self._report_with_practices())
        assert "◇ Untracked work: #91 carries no ticket reference" in text

    def test_plaintext_shows_the_team_rollup(self):
        text = render.format_standup_plaintext(self._report_with_practices())
        assert "Practices: Untracked work ×2" in text

    def test_plaintext_marks_a_repeat(self):
        text = render.format_standup_plaintext(self._report_with_practices(repeat=True))
        assert "Untracked work (again today):" in text

    def test_a_report_with_no_practices_says_nothing(self):
        text = render.format_standup_plaintext(_report())
        assert "◇" not in text
        assert "Practices:" not in text

    def test_rich_renders_them_too(self):
        from rich.console import Console

        console = Console(width=100, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(self._report_with_practices()))
        assert "Untracked work" in cap.get()

    def test_a_legacy_report_without_the_field_still_renders(self):
        # Reports serialized before this feature deserialize with practices=(),
        # but a hand-built object may not have the attribute at all.
        legacy = StandupReport(date="2026-07-10", member_updates=(MemberUpdate(name="Alice", summary="x"),))
        assert "Alice" in render.format_standup_plaintext(legacy)


_OPS = (
    OpsSignal(
        kind="incident",
        source="pagerduty",
        count=2,
        resolved=1,
        severity="high",
        services=("checkout",),
        window_start="2026-06-26T00:00:00+00:00",
        window_end="2026-07-10T00:00:00+00:00",
        samples=("Checkout latency above SLO",),
    ),
)


class TestProduction:
    def test_nothing_at_all_when_no_ops_vendor_is_connected(self):
        # Not a quieter section — no section. A broadcast surface must not
        # carry a heading announcing that a feature exists.
        text = render.format_standup_plaintext(_report())
        assert "Production" not in text

    def test_the_block_names_counts_and_the_window(self):
        report = dataclasses.replace(_report(), ops_signals=_OPS)
        text = render.format_standup_plaintext(report)
        assert "Production (since 2026-06-26)" in text
        assert "2 incidents via pagerduty" in text
        assert "1 resolved" in text
        assert "Checkout latency above SLO" in text

    def test_it_reads_before_the_per_person_updates(self):
        text = render.format_standup_plaintext(dataclasses.replace(_report(), ops_signals=_OPS))
        assert text.index("Production") < text.index("Updates:")

    def test_nobody_is_named_in_it(self):
        lines = render._production_lines(dataclasses.replace(_report(), ops_signals=_OPS))
        assert not any("Alice" in line or "Bob" in line for line in lines)

    def test_the_rich_form_carries_it_too(self):
        group = render.format_standup_rich(dataclasses.replace(_report(), ops_signals=_OPS))
        text = "\n".join(t.plain for t in group.renderables)
        assert "Production (since 2026-06-26)" in text
        assert "2 incidents via pagerduty" in text
