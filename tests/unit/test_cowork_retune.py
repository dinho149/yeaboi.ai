"""Standing guards over the one workstream whose subject is the fleet.

`fleet` owns `cowork/`, which means a routine can now edit the instructions
routines run under. Everything that makes that acceptable is structural rather
than behavioural, and every piece of it is one careless markdown edit away from
being untrue:

* **the constitution is outside every charter** — checked against `fleet`'s
  *resolved paths* rather than against the sentence in its prose, so a reworded
  exclusion or a widened glob fails instead of reading fine;
* **the auto lane is two append-only files** — the tighten half of "a routine
  may tighten itself unattended; only a human may loosen it", and both sites
  must be inside the charter or the rule describes work nobody may do;
* **no routine reads the run ledger** — outcomes are durable records of
  decisions and every sweep reads those, but run telemetry is what the fleet
  *spent*, and a routine deciding from it makes the fleet's behaviour a function
  of its own resource consumption.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cowork_setup  # noqa: E402
import hygiene_lens as lens  # noqa: E402

RETUNE = REPO_ROOT / "cowork" / "routines" / "cron" / "retune.md"
CHARTER = REPO_ROOT / "cowork" / "workstreams" / "fleet.md"
CALIBRATION = REPO_ROOT / "cowork" / "calibration.md"
ROUTINES = REPO_ROOT / "cowork" / "routines"


@pytest.fixture(scope="module")
def fleet() -> lens.Charter:
    return lens.charter("fleet")


class TestTheConstitutionIsOutsideEveryCharter:
    @pytest.mark.parametrize("path", cowork_setup.CONSTITUTION_GUARDS)
    def test_the_guards_over_the_constitution_are_out_of_reach_too(self, path: str) -> None:
        """Excluding a rule while leaving its enforcement editable is not an exclusion.

        `fleet` owns `tests/unit/test_cowork_*.py`, which swept in
        `test_cowork_models.py` (what makes `models.md` the only file naming a
        model) and `test_cowork_retune.py` (what asserts CONSTITUTION against the
        resolved charter paths — this file). Both are now subtracted in
        `fleet.md`'s **Owns**, and this asserts the resolved paths rather than the
        prose, the same way the documents themselves are asserted.
        """
        spec = lens.charter("fleet")
        assert not spec.covers(REPO_ROOT / path), (
            f"{path} guards the constitution and resolves inside fleet's Owns — a fleet run could "
            "edit the test that stops it editing the rules"
        )

    @pytest.mark.parametrize("path", cowork_setup.CONSTITUTION)
    def test_it_exists(self, path):
        """A guard over a path that moved is a guard that passes on nothing."""
        assert (REPO_ROOT / path).is_file(), path

    @pytest.mark.parametrize("path", cowork_setup.CONSTITUTION)
    def test_fleet_cannot_reach_it(self, fleet, path):
        assert not fleet.covers(REPO_ROOT / path), f"fleet's charter resolves to cover {path}"

    @pytest.mark.parametrize("workstream", sorted(cowork_setup.parse_workstreams()))
    def test_no_charter_at_all_can_reach_it(self, workstream):
        """`fleet` is the one that could, but the rule is not about `fleet`."""
        spec = lens.charter(workstream)
        for path in cowork_setup.CONSTITUTION:
            assert not spec.covers(REPO_ROOT / path), f"{workstream} covers {path}"

    def test_the_crew_agents_are_all_on_the_list(self):
        """Named individually, so a fourth crew agent is a deliberate decision."""
        on_disk = {p.name for p in (REPO_ROOT / ".claude" / "agents").glob("cowork-*.md")}
        listed = {Path(p).name for p in cowork_setup.CONSTITUTION if p.startswith(".claude/")}
        assert on_disk == listed

    def test_the_charter_says_so_as_well_as_resolving_that_way(self):
        text = CHARTER.read_text()
        for path in cowork_setup.CONSTITUTION:
            assert Path(path).name in text, f"fleet.md never mentions {path}"


class TestTheAsymmetry:
    """Tightening is unattended; loosening is a human's. Both halves are asserted."""

    @pytest.mark.parametrize("site", cowork_setup.AUTO_LANE_SITES)
    def test_every_auto_lane_site_exists_and_is_inside_the_charter(self, fleet, site):
        assert (REPO_ROOT / site).is_file(), site
        assert fleet.covers(REPO_ROOT / site), f"{site} is auto-lane but outside fleet's paths"

    @pytest.mark.parametrize("site", cowork_setup.AUTO_LANE_SITES)
    def test_the_routine_names_the_site_it_may_edit(self, site):
        assert Path(site).name in RETUNE.read_text(), site

    def test_the_rule_is_stated_in_both_places_a_run_reads(self):
        rule = "may tighten itself unattended"
        assert rule in CHARTER.read_text()
        assert rule in RETUNE.read_text()

    def test_a_charter_re_aim_is_explicitly_a_proposal(self):
        """The digest has described this fix for months; it is still a human's call."""
        text = RETUNE.read_text()
        assert "charter re-aim" in text.lower()
        assert "propose" in text.lower()

    def test_the_auto_lane_list_is_short_enough_to_hold_in_mind(self):
        """Not style. Each entry is a decision about what a machine may do to its
        own instructions, and a list nobody re-reads is one nobody audits."""
        assert len(cowork_setup.AUTO_LANE_SITES) <= 3


class TestNoRoutineReadsTheRunLedger:
    def _routine_files(self) -> list[Path]:
        return sorted(ROUTINES.rglob("*.md"))

    def test_not_one_of_them_names_it(self):
        for path in self._routine_files():
            assert cowork_setup.LEDGER_LABEL not in path.read_text(), path.name

    @pytest.mark.parametrize("name", ["retune.md", "digest.md"])
    def test_the_two_that_run_the_metrics_pass_no_runs(self, name):
        """The seam that keeps the rule absolute rather than nuanced.

        `cowork_metrics.py` reads the ledger by default because a human on a
        terminal wants cost per merged PR. A routine invoking it without this
        flag would read it too, and the rule would quietly become "no routine
        reads it *to decide anything*" — which no test can check.
        """
        text = (ROUTINES / "cron" / name).read_text()
        assert "cowork_metrics.py" in text, f"{name} no longer runs the metrics"
        # Invocations only. A sentence naming the script is prose; a line a
        # routine is told to run is the thing that can read the ledger.
        commands = [
            line for line in text.splitlines() if line.strip().startswith("uv run") and "cowork_metrics" in line
        ]
        assert commands, f"{name} names the metrics but never invokes them"
        for line in commands:
            assert "--no-runs" in line, f"{name} runs the metrics without --no-runs: {line.strip()}"

    def test_the_flag_actually_skips_the_read(self, monkeypatch):
        """And says so, rather than reporting an empty ledger as no runs."""
        import cowork_metrics as metrics

        monkeypatch.setattr(metrics, "fetch_merged_prs", lambda slug, since: ([], ""))
        monkeypatch.setattr(metrics, "fetch_proposals", lambda slug, since: ([], ""))
        monkeypatch.setattr(metrics, "fetch_markers", lambda slug, numbers: ({}, {}, ""))
        monkeypatch.setattr(metrics, "fetch_runs", lambda slug, since: pytest.fail("--no-runs still read the ledger"))
        report, warnings = metrics.collect("o/r", window=30, runs=False)
        assert report["runs"] == 0
        assert any("run ledger not read" in w for w in warnings)


class TestCalibrationIsTheOneThingReadBack:
    def test_every_scout_is_told_to_read_its_section(self):
        """The self-healing mechanism, and the whole reason this file is not a ledger."""
        assert "calibration.md" in (REPO_ROOT / ".claude" / "agents" / "cowork-scout.md").read_text()
        assert "calibration.md" in (REPO_ROOT / "cowork" / "sweep-procedure.md").read_text()

    def test_it_can_only_ever_narrow(self):
        text = CALIBRATION.read_text()
        assert "only ever **add** constraints" in text
        assert "only narrows" in text

    def test_a_row_without_an_issue_number_is_refused_in_writing(self):
        """It is read by something that cannot tell a fact from a rumour."""
        assert "rumour" in CALIBRATION.read_text()

    def test_nothing_deletes_a_row(self):
        assert "never deleted or rewritten unattended" in CALIBRATION.read_text().replace(
            "nothing here is ever deleted or rewritten unattended",
            "never deleted or rewritten unattended",
        )
        assert "Never delete or rewrite an existing" in RETUNE.read_text()


class TestTheSixteenthWorkstreamIsRegistered:
    def test_it_is_a_workstream(self):
        assert "fleet" in cowork_setup.parse_workstreams()
        assert len(cowork_setup.parse_workstreams()) == 16

    def test_it_has_a_label(self):
        assert "workstream:fleet" in {label.name for label in cowork_setup.expected_labels()}

    def test_its_routine_owns_its_steps_and_still_checks_in(self):
        """A `## Run` heading means it does not inherit `sweep-procedure.md`'s."""
        assert any(line.strip() == "## Run" for line in RETUNE.read_text().splitlines())
        assert cowork_setup.routines_without_check_in() == []

    def test_it_claims_no_source_module(self):
        """Its subject is `cowork/`; a `src/yeaboi/` claim would overlap a real charter."""
        spec = lens.charter("fleet")
        assert not any("src/yeaboi" in lens._relative(p) for p in spec.owns)
