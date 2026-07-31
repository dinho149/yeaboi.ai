"""Unit tests for html_exporter — the plan payload.

The page is drawn by ``frontend/src/export`` from a JSON island, so what this
module asserts is the *payload*: that every artifact the pipeline has produced
so far reached it, correctly shaped. How a story card looks, and which colour a
priority wears, are asserted in ``Plan.test.tsx``.
"""

from __future__ import annotations

from tests._pages import assert_self_contained, island
from yeaboi.html_exporter import build_export_html


class TestAttachments:
    def test_embeds_pasted_images(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        images = island(build_export_html({"pasted_images": [str(img)]}))["report"]["images"]
        assert len(images) == 1 and images[0].startswith("data:image/png;base64,")

    def test_deduplicates_across_state_fields(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        state = {"pasted_images": [str(img)], "chat_images": [str(img)]}
        assert len(island(build_export_html(state))["report"]["images"]) == 1

    def test_empty_when_no_images(self):
        assert island(build_export_html({}))["report"]["images"] == []

    def test_missing_files_are_skipped(self, tmp_path):
        state = {"chat_images": [str(tmp_path / "gone.png")]}
        assert island(build_export_html(state))["report"]["images"] == []

    def test_oversized_file_is_skipped(self, tmp_path, monkeypatch):
        import yeaboi.html_theme as theme

        monkeypatch.setattr(theme, "_MAX_EMBED_BYTES", 4)
        img = tmp_path / "big.png"
        img.write_bytes(b"12345")
        assert island(build_export_html({"pasted_images": [str(img)]}))["report"]["images"] == []


class TestSharedDesignSystem:
    def test_plan_html_uses_shared_theme(self):
        html = build_export_html({}, stage="complete")
        assert 'data-theme="midnight"' in html
        assert 'data-mode="planning"' in html  # the accent, set before first paint
        assert_self_contained(html)

    def test_empty_state_is_a_valid_plan(self):
        # A plan exported before anything ran is a normal artifact, not a broken
        # one — every section is an empty list rather than an absent key.
        report = island(build_export_html({}))["report"]
        assert report["questionnaire"] == []
        assert report["analysis"] is None
        assert report["features"] == report["storyGroups"] == report["taskGroups"] == report["sprints"] == []

    def test_badges_say_which_artifacts_exist(self):
        boot = island(build_export_html(_state()))
        assert boot["chrome"]["badges"] == ["Stories"]
        assert dict(tuple(f) for f in boot["chrome"]["facts"])["STAGE"] == "Complete Plan"

    def test_nav_lists_only_the_sections_that_exist(self):
        nav = [tuple(e) for e in island(build_export_html(_state()))["chrome"]["nav"]]
        assert nav == [("stories", "Stories")]


def _state():
    from yeaboi.agent.state import Discipline, Priority, StoryPointValue, UserStory

    def story(sid, discipline, pts):
        return UserStory(
            id=sid,
            feature_id="F-1",
            persona="user",
            goal="do things",
            benefit="value",
            acceptance_criteria=(),
            story_points=StoryPointValue(pts),
            priority=Priority.MEDIUM,
            title=f"Story {sid}",
            discipline=discipline,
        )

    return {
        "project_description": "Demo",
        "stories": [
            story("S-1", Discipline.BACKEND, 5),
            story("S-2", Discipline.BACKEND, 3),
            story("S-3", Discipline.FRONTEND, 8),
        ],
        "features": [],
    }


def _analysis(**over):
    from yeaboi.agent.state import ProjectAnalysis

    base = dict(
        project_name="Demo",
        project_description="Demo",
        project_type="greenfield",
        goals=(),
        end_users=(),
        target_state="",
        tech_stack=(),
        integrations=(),
        constraints=(),
        sprint_length_weeks=2,
        risks=(),
        out_of_scope=(),
        assumptions=(),
        target_sprints=3,
    )
    base.update(over)
    return ProjectAnalysis(**base)


class TestStories:
    def test_points_by_discipline(self):
        report = island(build_export_html(_state()))["report"]
        assert report["pointsByDiscipline"] == [["backend", 8], ["frontend", 8]]

    def test_stories_group_under_their_feature(self):
        groups = island(build_export_html(_state()))["report"]["storyGroups"]
        assert len(groups) == 1
        assert groups[0]["featureId"] == "F-1"
        # No feature record exists, so the id stands in for the title.
        assert groups[0]["featureTitle"] == "F-1"
        assert [s["id"] for s in groups[0]["stories"]] == ["S-1", "S-2", "S-3"]

    def test_dod_travels_already_paired(self):
        # Zipped here rather than as two lists: the old renderer zipped them
        # behind a length check, and a mismatch dropped the block in silence.
        story = island(build_export_html(_state()))["report"]["storyGroups"][0]["stories"][0]
        assert all(len(pair) == 2 and isinstance(pair[1], bool) for pair in story["dod"])

    def test_mismatched_dod_flags_yield_no_pairs(self):
        from dataclasses import replace

        state = _state()
        state["stories"] = [replace(state["stories"][0], dod_applicable=(True,))]
        story = island(build_export_html(state))["report"]["storyGroups"][0]["stories"][0]
        assert story["dod"] == []


class TestCapacity:
    def _capacity_state(self):
        state = _state()
        state.update(
            {
                "project_analysis": _analysis(),
                "team_size": 4,
                "velocity_per_sprint": 40,
                "net_velocity_per_sprint": 30,
                "sprint_length_weeks": 2,
            }
        )
        return state

    def test_gross_net_and_deductions(self):
        capacity = island(build_export_html(self._capacity_state()))["report"]["capacity"]
        assert capacity["velocity"] == 40 and capacity["netVelocity"] == 30
        assert capacity["teamSize"] == 4
        # The default 5% discovery allowance is a deduction like any other.
        assert capacity["deductions"] == ["discovery: 5%"]

    def test_absent_without_the_numbers_to_show_it(self):
        # "0 pts/sprint" reads as a finding rather than as a gap.
        state = self._capacity_state()
        state["velocity_per_sprint"] = 0
        assert island(build_export_html(state))["report"]["capacity"] is None


class TestAnalysis:
    def test_only_populated_fields_travel(self):
        state = _state()
        state["project_analysis"] = _analysis(goals=("Ship it",), risks=("Scope creep", "Vendor lock-in"))
        analysis = island(build_export_html(state))["report"]["analysis"]
        assert [f["label"] for f in analysis["fields"]] == ["Goals", "Risks"]
        assert analysis["fields"][1]["items"] == ["Scope creep", "Vendor lock-in"]

    def test_epic_key_from_either_tracker(self):
        state = _state()
        state["project_analysis"] = _analysis()
        state["azdevops_epic_id"] = "12345"
        assert island(build_export_html(state))["report"]["epicKey"] == "12345"
