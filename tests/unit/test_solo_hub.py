"""Tests for the Weekly Review saved-runs hub (`_run_solo_review_hub`).

The hub is wiring: injected callables over WeeklyReviewStore plus the detail
screen in snapshot mode. What is worth testing is exactly that wiring — the
card list is built from stored reviews, opening one renders the detail view,
the document is the Markdown export, and delete removes the row.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console as RichConsole

from yeaboi.agent.state import ReviewAction, WeeklyReview
from yeaboi.solo.store import WeeklyReviewStore


def _render(panel, width: int = 100, height: int = 40) -> str:
    console = RichConsole(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    """Capture the hub's injected callables instead of running its loop."""
    import yeaboi.ui.mode_select as mode_select

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    with WeeklyReviewStore(db) as store:
        store.record_run(
            WeeklyReview(
                week_label="2026-W35",
                week_start="2026-08-24",
                week_end="2026-08-28",
                project_name="Demo",
                session_id="s1",
                plan_line="No sprint plan on file",
                summary="Quiet week.",
                actions=(ReviewAction(id="a1", text="Write the spike", week_label="2026-W35"),),
            )
        )
        store.record_run(
            WeeklyReview(
                week_label="2026-W36",
                week_start="2026-08-31",
                week_end="2026-09-04",
                project_name="Demo",
                session_id="s1",
                plan_status="on_track",
                plan_line="Day 4/10 · On track",
                summary="Busy week.",
                actions=(
                    ReviewAction(id="b1", text="Ship OAuth", week_label="2026-W36"),
                    ReviewAction(id="b2", text="Cut the backlog", week_label="2026-W36"),
                ),
            )
        )

    captured: dict = {}

    def _fake_hub(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mode_select, "_run_mode_hub", _fake_hub)
    mode_select._run_solo_review_hub(object(), object(), lambda **_k: "esc", 0.001, True)
    return captured, db


class TestHubWiring:
    def test_registered_under_the_card_key(self):
        import yeaboi.ui.mode_select as mode_select

        assert mode_select.SAVED_SESSION_HUBS["weekly-review"] is mode_select._run_solo_review_hub

    def test_lists_reviews_newest_first(self, hub):
        captured, _db = hub
        runs = captured["load_runs"]()
        assert [r.title for r in runs] == ["Week 2026-W36", "Week 2026-W35"]
        assert runs[0].subtitle == "Demo · 2 actions"
        assert runs[1].subtitle == "Demo · 1 action"
        assert captured["mode"] == "solo"
        assert captured.get("get_share_document") is None  # no Share: the review is yours alone

    def test_opening_a_run_renders_the_detail_view(self, hub):
        captured, _db = hub
        run = captured["load_runs"]()[0]
        render = captured["make_detail"](run)
        panel = render(
            scroll=0,
            action_sel=0,
            actions=["Back"],
            scroll_meta={},
            width=100,
            height=40,
            message="",
            shimmer_tick=None,
        )
        out = _render(panel)
        assert "Busy week." in out
        assert "Ship OAuth" in out
        assert "Day 4/10" in out

    def test_a_deleted_run_no_longer_opens(self, hub):
        captured, db = hub
        run = captured["load_runs"]()[1]
        captured["delete_run"](run)
        assert [r.title for r in captured["load_runs"]()] == ["Week 2026-W36"]
        assert captured["make_detail"](run) is None
        assert captured["get_document"](run) == "That run is no longer available."

    def test_document_is_the_markdown_export(self, hub):
        captured, _db = hub
        run = captured["load_runs"]()[0]
        title, markdown = captured["get_document"](run)
        assert title == "Weekly Review — 2026-W36"
        assert "Ship OAuth" in markdown

    def test_files_export_writes_markdown(self, hub, tmp_path, monkeypatch):
        captured, _db = hub
        monkeypatch.setattr("yeaboi.paths.SOLO_EXPORTS_DIR", tmp_path / "exports")
        run = captured["load_runs"]()[0]
        message = captured["files_export"](run)
        assert "Exported to" in message
        assert (tmp_path / "exports" / "Demo" / "weekly-review-2026-W36.md").exists()
