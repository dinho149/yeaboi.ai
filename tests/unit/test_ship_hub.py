"""Tests for the Ship saved-runs hub (`_run_ship_hub`).

The hub is wiring: injected callables over ShipStore plus the gate screen in
snapshot mode. So what is worth testing is exactly the wiring — that the card
list is built from real stored runs, that a stranded run is marked resumable,
and that opening a run renders the snapshot without the live gate's buttons.
The pieces underneath have their own suites.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console as RichConsole

from yeaboi.agent.state import ShipRun, ShipValidation
from yeaboi.ship.store import ShipStore


class _Live:
    def __init__(self):
        self.frames = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (100, 40)


def _keys(*sequence):
    """A scripted reader; falls back to esc forever so no loop can hang."""
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""
        return remaining.pop(0) if remaining else "esc"

    return _read


def _render(panel, width: int = 100, height: int = 40) -> str:
    console = RichConsole(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


@pytest.fixture()
def hub_db(tmp_path, monkeypatch):
    """A store with two runs — one finished, one stranded at the gate."""
    import yeaboi.ui.mode_select as mode_select

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    with ShipStore(db) as store:
        store.record_run(
            ShipRun(
                run_id="us-001-20260801120000-aaa111",
                item_id="US-001",
                repo=str(tmp_path / "proj"),
                branch="ship/us-001",
                status="approved",
                diff_stat=" a.py | 2 +-\n 1 file changed, 1 insertion(+)",
                validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
                cost_usd=0.42,
                pr_url="https://github.com/o/r/pull/1",
                created_at="2026-08-01T12:00:00+00:00",
            )
        )
        store.record_run(
            ShipRun(
                run_id="us-002-20260802120000-bbb222",
                item_id="US-002",
                repo=str(tmp_path / "proj"),
                branch="ship/us-002",
                status="awaiting_approval",
                diff_stat=" b.py | 9 +++++\n 1 file changed, 9 insertions(+)",
                diff_text="diff --git a/b.py b/b.py\n+added\n",
                owner_pid=424242,
                created_at="2026-08-02T12:00:00+00:00",
            )
        )
    return db


def _open_hub(hub_db, keys, monkeypatch, *, resumable=False):
    """Drive `_run_ship_hub` with a scripted key sequence; return the frames."""
    from yeaboi.ship import engine
    from yeaboi.ui.mode_select import _run_ship_hub

    monkeypatch.setattr(engine, "_resumable_reason", lambda run: "" if resumable else "not resumable")
    live = _Live()
    _run_ship_hub(_Console(), live, _keys(*keys), 0.05, True)
    return live.frames


class TestTheList:
    def test_saved_runs_are_listed_newest_first(self, hub_db, monkeypatch):
        frames = _open_hub(hub_db, ["esc"], monkeypatch)
        out = _render(frames[-1])
        assert "US-002" in out and "US-001" in out
        assert out.index("US-002") < out.index("US-001")

    def test_a_row_carries_the_branch_and_the_diff_total(self, hub_db, monkeypatch):
        out = _render(_open_hub(hub_db, ["esc"], monkeypatch)[-1])
        assert "ship/us-002" in out
        assert "1 file changed" in out

    def test_the_resumable_marker_leads_the_subtitle_so_it_survives_cropping(self, hub_db, monkeypatch):
        # The card subtitle is cropped to the card width; a marker appended after
        # the branch and the diff stat is cut off exactly when it matters.
        out = _render(_open_hub(hub_db, ["esc"], monkeypatch, resumable=True)[-1])
        assert out.index("resumable") < out.index("ship/us-002")

    def test_a_stranded_run_is_marked_resumable(self, hub_db, monkeypatch):
        out = _render(_open_hub(hub_db, ["esc"], monkeypatch, resumable=True)[-1])
        assert "resumable" in out

    def test_a_finished_run_is_not_marked_resumable(self, hub_db, monkeypatch):
        out = _render(_open_hub(hub_db, ["esc"], monkeypatch, resumable=False)[-1])
        assert "resumable" not in out

    def test_an_empty_store_says_so_rather_than_rendering_a_blank_list(self, tmp_path, monkeypatch):
        import yeaboi.ui.mode_select as mode_select

        monkeypatch.setattr(mode_select, "_ana_dbp", tmp_path / "empty.db")
        from yeaboi.ship import engine
        from yeaboi.ui.mode_select import _run_ship_hub

        monkeypatch.setattr(engine, "_resumable_reason", lambda run: "no")
        live = _Live()
        _run_ship_hub(_Console(), live, _keys("esc"), 0.05, True)
        assert "No ship runs yet" in _render(live.frames[-1])


class TestOpeningARun:
    def test_enter_opens_the_snapshot_with_hub_buttons(self, hub_db, monkeypatch):
        # enter opens the newest run's snapshot; the two escs leave it and the hub,
        # so the snapshot is a frame in the middle, not the last one.
        frames = [_render(f) for f in _open_hub(hub_db, ["enter", "esc", "esc"], monkeypatch)]
        assert any("Export" in f and "Back" in f for f in frames)
        # The live gate's controls must never appear over a finished run.
        assert not any("Approve" in f for f in frames)

    def test_a_resumable_run_offers_resume(self, hub_db, monkeypatch):
        frames = _open_hub(hub_db, ["enter", "esc", "esc"], monkeypatch, resumable=True)
        assert any("Resume" in _render(f) for f in frames)

    def test_the_snapshot_shows_the_stored_patch(self, hub_db, monkeypatch):
        frames = _open_hub(hub_db, ["enter", "esc", "esc"], monkeypatch)
        assert any("added" in _render(f) for f in frames)


class TestExportAndDelete:
    def test_export_writes_a_markdown_record(self, hub_db, monkeypatch, tmp_path):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "SHIP_EXPORTS_DIR", tmp_path / "exports")
        from yeaboi.ship.export import export_ship

        with ShipStore(hub_db) as store:
            run = store.get_run("us-001-20260801120000-aaa111")
            events = store.gate_events(run.run_id)
        written = export_ship(run, gate_events=events)["markdown"]
        assert "US-001" in written.read_text(encoding="utf-8")

    def test_delete_removes_the_run_from_the_next_listing(self, hub_db, monkeypatch):
        with ShipStore(hub_db) as store:
            assert store.delete_run("us-002-20260802120000-bbb222") is True
        out = _render(_open_hub(hub_db, ["esc"], monkeypatch)[-1])
        assert "US-002" not in out
        assert "US-001" in out


@pytest.fixture()
def batch_db(tmp_path, monkeypatch):
    """A store holding one two-member batch and nothing else."""
    import yeaboi.ui.mode_select as mode_select

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    with ShipStore(db) as store:
        for index, story in enumerate(("US-1", "US-2"), start=1):
            store.record_run(
                ShipRun(
                    run_id=f"r{index}",
                    item_id=story,
                    level="story",
                    repo="/tmp/proj",
                    branch=f"ship/{story.lower()}",
                    status="approved",
                    validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
                    batch_id="b1",
                    batch_item_id="F1",
                    batch_index=index,
                    batch_total=2,
                    created_at=f"2026-08-0{index}T12:00:00",
                )
            )
    return db


class TestBatchRows:
    def test_a_batch_is_one_row_that_counts_what_shipped(self, batch_db, monkeypatch):
        out = _render(_open_hub(batch_db, ["esc"], monkeypatch)[-1])
        assert "F1" in out
        assert "2/2" in out

    def test_opening_the_row_lists_its_members(self, batch_db, monkeypatch):
        # The last frame is the hub list we backed out to, so look at the frames
        # the batch view painted before the escapes.
        frames = [_render(f) for f in _open_hub(batch_db, ["enter", "esc", "esc"], monkeypatch)]
        assert any("US-1" in f and "US-2" in f and "Batch F1" in f for f in frames)

    def test_deleting_one_member_keeps_the_rest_of_the_batch(self, batch_db, monkeypatch):
        # The hub binds its action closure to the row it opened — the whole
        # batch. Reused inside a member, Delete would wipe every stacked run.
        _open_hub(batch_db, ["enter", "enter", "right", "enter"], monkeypatch)
        with ShipStore(batch_db) as store:
            assert [r.run_id for r in store.batch_runs("b1")] == ["r2"]

    def test_deleting_the_whole_batch_from_the_list_still_takes_every_member(self, batch_db, monkeypatch):
        # The batch row itself is the one place that means "all of them".
        with ShipStore(batch_db) as store:
            for run in store.batch_runs("b1"):
                store.delete_run(run.run_id)
            assert store.batch_runs("b1") == []
