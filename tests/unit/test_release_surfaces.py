"""Tests for scripts/release_surfaces.py — the hand-test checklist.

This table is the only part of the release gate that is *advice* rather than a
check, so its failure mode is quiet: a row that never fires reads exactly like a
week where nothing risky changed. Every test below is about firing — that each
row is reachable from a path that really exists in this repository, that the
baseline is unconditional, and that a row cannot fire twice and pad the list
until it stops being read.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("release_surfaces", ROOT / "scripts" / "release_surfaces.py")
surfaces = importlib.util.module_from_spec(_spec)
sys.modules["release_surfaces"] = surfaces
_spec.loader.exec_module(surfaces)


def labels(paths: list[str]) -> list[str]:
    return [item.label for item in surfaces.checklist(paths)]


class TestBaseline:
    def test_it_fires_for_nothing_at_all(self):
        assert labels([]) == [item.label for item in surfaces.BASELINE]

    def test_it_is_never_empty(self):
        """An empty checklist reads as 'signed off' when it means 'not asked'."""
        assert surfaces.BASELINE

    def test_it_still_fires_alongside_everything_else(self):
        found = labels(["src/yeaboi/ui/mode_select/__init__.py"])
        assert found[: len(surfaces.BASELINE)] == [item.label for item in surfaces.BASELINE]


class TestRowsFire:
    def test_every_row_is_reachable_from_its_own_patterns(self):
        """A pattern nobody can match is a row that silently never fires."""
        for patterns, item in surfaces.SURFACES:
            sample = patterns[0].replace("/**", "/example.py").replace("*", "x")
            assert item.label in labels([sample]), f"{item.label} unreachable via {patterns[0]}"

    def test_a_frontend_change_asks_for_the_off_lan_check(self):
        assert "browser" in labels(["frontend/src/board/App.tsx"])
        assert "browser" in labels(["src/yeaboi/web/static/board.js"])

    def test_the_scheduler_asks_for_a_real_fire(self):
        assert "schedule" in labels(["src/yeaboi/standup/scheduler.py"])
        assert "schedule" not in labels(["src/yeaboi/standup/engine.py"])

    def test_a_mirrored_module_asks_for_both_sidecar_paths(self):
        for path in (
            "go/internal/analysis/aggregate.go",
            "src/yeaboi/agentwatch/collector.py",
            "src/yeaboi/standup/aggregate.py",
            "src/yeaboi/sessions.py",
        ):
            assert "sidecar" in labels([path]), path

    def test_an_unrelated_path_adds_nothing(self):
        assert labels(["docs/index.html", "README.md", ".github/dependabot.yml"]) == labels([])


class TestIntegrationAngles:
    """The campaign half of the checklist, which works the opposite way round.

    `SURFACES` answers "test what changed", which is a maintenance question. A
    week-long coverage campaign asks the reverse — *which modes did we not reach
    yet* — so every angle is listed for a provider in the batch, and the untouched
    ones are marked rather than dropped. An angle that vanishes is indistinguishable
    from an angle that was never needed, which is the failure this file exists to
    prevent.
    """

    def test_every_angle_is_reachable_from_its_own_patterns(self):
        """The mirror of `test_every_row_is_reachable_from_its_own_patterns`.

        That one cannot cover this table — it samples `patterns[0]` and these rows
        are only ever asked about alongside a provider module — so the same
        silently-never-fires failure needed its own guard on the new door.
        """
        for patterns, item in surfaces.INTEGRATION_ANGLES:
            sample = patterns[0].replace("/**", "/example.py").replace("*", "x")
            reached = {
                found.label
                for found in surfaces.integration_checklist(("gitlab",), ["src/yeaboi/tools/gitlab.py", sample])
                if found.reached
            }
            assert item.label in reached, f"{item.label} unreachable via {patterns[0]}"

    def test_no_provider_means_no_integration_session(self):
        """Empty, not baseline-only: `batch_view` reads empty as 'never asked'."""
        assert surfaces.campaign_providers(["src/yeaboi/ui/x.py"]) == ()
        assert surfaces.integration_checklist((), ["src/yeaboi/ui/x.py"]) == []

    def test_the_non_provider_modules_are_not_providers(self):
        """`risk.py` and friends live in `tools/` and talk to no external service."""
        paths = [f"src/yeaboi/tools/{stem}.py" for stem in surfaces._NOT_PROVIDERS]
        assert surfaces.campaign_providers(paths) == ()

    def test_two_providers_are_both_named(self):
        """Returning the first would silently drop the second from the checklist."""
        assert surfaces.campaign_providers(["src/yeaboi/tools/gitlab.py", "src/yeaboi/tools/jira.py"]) == (
            "gitlab",
            "jira",
        )

    def test_a_nested_path_under_tools_is_not_a_provider(self):
        assert surfaces.campaign_providers(["src/yeaboi/tools/vendor/gitlab.py"]) == ()

    def test_an_unreached_angle_is_listed_and_not_tickable(self):
        items = surfaces.integration_checklist(("gitlab",), ["src/yeaboi/tools/gitlab.py"])
        unreached = [item for item in items if not item.reached]
        assert unreached, "a client-only batch reaches almost no mode"
        assert "not wired in this batch" in surfaces.render(unreached, markdown=False)
        assert "[ ]" not in surfaces.render(unreached, markdown=False)

    def test_every_angle_names_its_provider(self):
        for item in surfaces.integration_checklist(("gitlab",), ["src/yeaboi/tools/gitlab.py"]):
            assert item.what.startswith("gitlab: ")
            assert item.track == "integration"


class TestTrackedChecklists:
    def test_the_baseline_is_shared_and_not_duplicated(self):
        baseline, tracks = surfaces.tracked_checklists(["src/yeaboi/tools/gitlab.py"])
        assert [item.label for item in baseline] == [item.label for item in surfaces.BASELINE]
        for items in tracks.values():
            assert not {item.label for item in items} & {item.label for item in surfaces.BASELINE}

    def test_a_maintenance_week_has_an_empty_integration_track(self):
        _, tracks = surfaces.tracked_checklists(["frontend/app.tsx"])
        assert [item.label for item in tracks["maintenance"]] == ["browser"]
        assert tracks["integration"] == []

    def test_the_generic_integrations_row_defers_to_the_angles(self):
        """Both ask for the same work, and a checklist that repeats itself is one
        nobody finishes. The per-angle list is the more specific of the two."""
        _, tracks = surfaces.tracked_checklists(["src/yeaboi/config.py"])
        assert "integrations" in [item.label for item in tracks["maintenance"]]
        _, tracks = surfaces.tracked_checklists(["src/yeaboi/tools/gitlab.py", "src/yeaboi/config.py"])
        assert "integrations" not in [item.label for item in tracks["maintenance"]]
        assert tracks["integration"]

    def test_an_override_reaches_a_batch_with_no_provider_module(self):
        """A campaign's reach angle touches only other workstreams' files.

        Nothing in the diff says `gitlab`, so path attribution alone would file the
        whole thing as maintenance and nobody would be asked to drive the provider
        anywhere. `release_channel` passes the commit-subject providers in here.
        """
        paths = ["src/yeaboi/standup/collector.py", "src/yeaboi/analysis/engine.py"]
        _, tracks = surfaces.tracked_checklists(paths)
        assert tracks["integration"] == []
        _, tracks = surfaces.tracked_checklists(paths, ("gitlab",))
        assert [item.label for item in tracks["integration"] if item.reached] == ["standup", "analysis"]

    def test_every_track_is_a_declared_one(self):
        _, tracks = surfaces.tracked_checklists(["src/yeaboi/tools/gitlab.py"])
        assert set(tracks) == set(surfaces.TRACKS)


class TestNoise:
    def test_a_row_fires_once_however_many_of_its_paths_changed(self):
        """A checklist that repeats itself is one nobody finishes."""
        many = [f"frontend/src/{name}.tsx" for name in ("a", "b", "c", "d")]
        assert labels(many).count("browser") == 1

    def test_every_item_carries_a_why(self):
        """A checklist item without a reason is the first one to get skipped."""
        for item in surfaces.checklist(["frontend/x.tsx", "src/yeaboi/cli.py"]):
            assert item.why.strip(), item.label
            assert item.what.strip(), item.label


class TestGlobs:
    def test_a_double_star_means_this_prefix_at_any_depth(self):
        assert surfaces._match("frontend/a/b/c.tsx", ("frontend/**",))
        assert surfaces._match("frontend", ("frontend/**",))
        assert not surfaces._match("frontend-extra/a.tsx", ("frontend/**",))

    def test_a_literal_path_does_not_match_a_sibling(self):
        assert surfaces._match("src/yeaboi/cli.py", ("src/yeaboi/cli.py",))
        assert not surfaces._match("src/yeaboi/cli_extra.py", ("src/yeaboi/cli.py",))


class TestRender:
    def test_markdown_is_a_tickable_task_list(self):
        body = surfaces.render(surfaces.checklist(["frontend/x.tsx"]), markdown=True)
        assert body.startswith("- [ ] ")
        assert "**browser**" in body

    def test_the_terminal_form_carries_no_markup(self):
        body = surfaces.render(surfaces.checklist([]), markdown=False)
        assert "- [ ]" not in body
        assert "[ ] install" in body
