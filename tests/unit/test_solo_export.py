"""Tests for the Weekly Review Markdown export (solo/export.py)."""

from __future__ import annotations

from yeaboi import paths
from yeaboi.agent.state import DeliveredItem, ReviewAction, WeeklyReview
from yeaboi.solo.export import build_weekly_review_markdown, export_weekly_review


def _review(**kw) -> WeeklyReview:
    base = dict(
        week_label="2026-W36",
        week_start="2026-08-31",
        week_end="2026-09-04",
        project_name="Apollo",
        my_name="Dinho",
        plan_line="Day 2/5 of Sprint 1 · On track",
        summary="Solid week.",
        went_well=("Shipped S-1",),
        to_change=("Ask earlier",),
        actions=(ReviewAction(id="a1", text="Split S-2", week_label="2026-W36"),),
        carried_actions=(
            ReviewAction(id="c1", text="Write docs", status="done", origin="carryover", week_label="2026-W35"),
            ReviewAction(id="c2", text="Fix CI", status="dropped", origin="carryover", week_label="2026-W35"),
        ),
        standup_lines=("Mon 2026-08-31: closed S-1",),
        delivered_items=(DeliveredItem(key="S-1", title="Login"),),
        warnings=("AI review unavailable — no key",),
        generated_at="2026-09-04T17:00:00+00:00",
    )
    base.update(kw)
    return WeeklyReview(**base)


class TestMarkdown:
    def test_sections_in_order(self):
        md = build_weekly_review_markdown(_review())
        order = [
            "# Weekly Review — Apollo — 2026-W36",
            "**Against the plan:** Day 2/5",
            "## Summary",
            "## What went well",
            "## What to change",
            "## Actions for next week",
            "## Carried from last week",
            "## Standups",
            "## Delivered",
            "## Notices",
            "_Generated 2026-09-04",
        ]
        positions = [md.index(s) for s in order]
        assert positions == sorted(positions)

    def test_action_glyphs_and_origin_tag(self):
        md = build_weekly_review_markdown(_review())
        assert "- [ ] Split S-2" in md
        assert "- [x] Write docs _(from 2026-W35)_" in md
        assert "- [-] Fix CI" in md

    def test_empty_review_still_renders_a_document(self):
        md = build_weekly_review_markdown(WeeklyReview(week_label="2026-W01"))
        assert md.startswith("# Weekly Review — Solo — 2026-W01")
        assert "no verdict" in md
        assert "##" not in md


class TestExport:
    def test_writes_one_file_per_week_under_the_project(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "SOLO_EXPORTS_DIR", tmp_path / "solo")
        out = export_weekly_review(_review())
        assert out == {"markdown": tmp_path / "solo" / "apollo" / "weekly-review-2026-W36.md"}
        assert out["markdown"].read_text(encoding="utf-8").startswith("# Weekly Review")

    def test_rerun_overwrites(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "SOLO_EXPORTS_DIR", tmp_path / "solo")
        export_weekly_review(_review(summary="first"))
        out = export_weekly_review(_review(summary="second"))
        assert "second" in out["markdown"].read_text(encoding="utf-8")

    def test_no_project_lands_in_the_solo_folder(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "SOLO_EXPORTS_DIR", tmp_path / "solo")
        out = export_weekly_review(_review(project_name=""))
        assert out["markdown"].parent == tmp_path / "solo" / "solo"
