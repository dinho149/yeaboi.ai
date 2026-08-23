"""Tests for the ship run record exporter (ship/export.py).

Two things matter here and nothing else does: the record says what actually
happened — including the honest zero states — and it is safe to publish, because
``get_document`` feeds the shared Notion/Confluence/copy picker.
"""

from __future__ import annotations

from yeaboi.agent.state import ShipPhase, ShipRun, ShipValidation
from yeaboi.ship.export import _stem, _title, build_ship_markdown, export_ship


def _run(**overrides) -> ShipRun:
    base = ShipRun(
        run_id="us-001-20260823120000-ab12cd",
        item_id="US-001",
        repo="/Users/someone/code/proj",
        branch="ship/us-001-20260823120000-ab12cd",
        base_sha="abcdef1234567890",
        status="approved",
        phases=(
            ShipPhase(name="setup", status="completed", detail="worktree ready", duration_s=0.4),
            ShipPhase(name="implement", status="completed", detail="agent ran", duration_s=192.0),
        ),
        validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
        diff_stat=" src/a.py | 3 +-\n 1 file changed, 2 insertions(+), 1 deletion(-)",
        cost_usd=1.25,
        pr_url="https://github.com/o/r/pull/291",
        created_at="2026-08-23T12:00:00+00:00",
    )
    return ShipRun(**{**base.__dict__, **overrides})


class TestMarkdown:
    def test_it_carries_the_run_facts(self):
        md = build_ship_markdown(_run())
        assert "US-001" in md
        assert "ship/us-001-20260823120000-ab12cd" in md
        assert "https://github.com/o/r/pull/291" in md
        assert "$1.25" in md
        assert "1 file changed" in md

    def test_phase_timings_render_in_minutes_and_seconds(self):
        md = build_ship_markdown(_run())
        assert "3m 12s" in md  # 192s
        assert "setup" in md

    def test_a_sub_second_phase_shows_no_timing_rather_than_zero(self):
        md = build_ship_markdown(_run())
        assert "(0s)" not in md

    def test_an_unconfigured_check_says_nothing_was_proven(self):
        md = build_ship_markdown(_run(validation=ShipValidation(configured=False)))
        assert "nothing was proven" in md

    def test_a_failed_check_names_the_exit_code(self):
        md = build_ship_markdown(
            _run(validation=ShipValidation(configured=True, command="make test", passed=False, exit_code=1))
        )
        assert "FAILED (exit 1)" in md

    def test_an_empty_diff_says_so_rather_than_rendering_a_blank_block(self):
        md = build_ship_markdown(_run(diff_stat=""))
        assert "No diff was recorded" in md

    def test_it_names_the_command_that_prints_the_patch(self):
        md = build_ship_markdown(_run())
        assert "git diff abcdef123456..ship/us-001-20260823120000-ab12cd" in md

    def test_the_gate_trail_is_rendered_oldest_first(self):
        md = build_ship_markdown(
            _run(),
            gate_events=[
                ("rejected", "needs a test", "2026-08-23T12:05:00+00:00"),
                ("approved", "", "2026-08-23T12:20:00+00:00"),
            ],
        )
        assert md.index("rejected") < md.index("**approved**")
        assert "needs a test" in md

    def test_an_unanswered_gate_is_stated_not_omitted(self):
        md = build_ship_markdown(_run(status="awaiting_approval", gate_resolution=""))
        assert "never answered" in md

    def test_security_findings_reach_the_record(self):
        md = build_ship_markdown(_run(transcript_findings=(("secret", "high", "aws key in a test fixture"),)))
        assert "aws key in a test fixture" in md
        assert "high" in md

    def test_warnings_reach_the_record(self):
        md = build_ship_markdown(_run(warnings=("the agent produced no result envelope",)))
        assert "no result envelope" in md


class TestItIsSafeToPublish:
    def test_the_home_directory_is_collapsed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", "/Users/someone")
        md = build_ship_markdown(_run(worktree="/Users/someone/.yeaboi/ship/worktrees/proj/r1"))
        assert "/Users/someone" not in md

    def test_a_secret_in_agent_output_is_redacted(self):
        leaked = "ghp_" + "a" * 36
        md = build_ship_markdown(
            _run(
                validation=ShipValidation(
                    configured=True, command="make test", passed=False, exit_code=1, output_tail=f"token={leaked}"
                )
            )
        )
        assert leaked not in md


class TestWriting:
    def test_it_writes_one_markdown_file_named_by_run(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "SHIP_EXPORTS_DIR", tmp_path / "exports")
        run = _run()
        paths_out = export_ship(run)
        assert set(paths_out) == {"markdown"}
        assert paths_out["markdown"].name == f"{_stem(run)}.md"
        assert "US-001" in paths_out["markdown"].read_text(encoding="utf-8")

    def test_re_exporting_overwrites_its_own_file(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "SHIP_EXPORTS_DIR", tmp_path / "exports")
        first = export_ship(_run())["markdown"]
        second = export_ship(_run(status="rejected"))["markdown"]
        assert first == second
        assert "rejected" in second.read_text(encoding="utf-8")

    def test_the_title_names_the_story(self):
        assert _title(_run()) == "Ship — US-001"
