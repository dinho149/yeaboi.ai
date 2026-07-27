"""Unit tests for html_exporter helpers — image embedding + plan attachments section."""

from __future__ import annotations

from yeaboi.html_exporter import _build_attachments_section, build_export_html, img_b64_tag


class TestImgB64Tag:
    def test_embeds_png_as_data_uri(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG fake")
        tag = img_b64_tag(img, alt="My <shot>")
        assert tag.startswith('<img src="data:image/png;base64,')
        assert 'alt="My &lt;shot&gt;"' in tag  # alt is escaped

    def test_missing_file_returns_empty(self, tmp_path):
        assert img_b64_tag(tmp_path / "gone.png") == ""

    def test_oversized_file_returns_empty(self, tmp_path, monkeypatch):
        import yeaboi.html_exporter as he

        monkeypatch.setattr(he, "_MAX_EMBED_BYTES", 4)
        img = tmp_path / "big.png"
        img.write_bytes(b"12345")
        assert img_b64_tag(img) == ""


class TestAttachmentsSection:
    def test_embeds_pasted_images(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        section = _build_attachments_section({"pasted_images": [str(img)]})
        assert "Attachments" in section
        assert "data:image/png;base64," in section

    def test_deduplicates_across_state_fields(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        section = _build_attachments_section({"pasted_images": [str(img)], "chat_images": [str(img)]})
        assert section.count("data:image/png;base64,") == 1

    def test_empty_when_no_images(self):
        assert _build_attachments_section({}) == ""

    def test_missing_files_yield_empty_section(self, tmp_path):
        assert _build_attachments_section({"chat_images": [str(tmp_path / "gone.png")]}) == ""

    def test_plan_html_includes_attachments(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"png")
        html = build_export_html({"pasted_images": [str(img)]}, stage="complete")
        assert "Attachments" in html
        assert "data:image/png;base64," in html


class TestSharedDesignSystem:
    def test_plan_html_uses_shared_theme(self):
        html = build_export_html({}, stage="complete")
        assert 'data-theme="midnight"' in html
        assert "yeaboi-export-theme" in html  # theme switcher present
        assert 'src="http' not in html and "<link" not in html  # self-contained


class TestVisuals:
    def _state(self):
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

    def test_points_by_discipline_bar(self):
        html = build_export_html(self._state())
        # class attribute, not the bare token — ".seg-track" also lives in the stylesheet.
        assert 'class="seg-track"' in html
        assert "backend 8" in html
        assert "frontend 8" in html

    def test_capacity_split_bar(self):
        from yeaboi.agent.state import ProjectAnalysis

        state = self._state()
        state.update(
            {
                "project_analysis": ProjectAnalysis(
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
                ),
                "team_size": 4,
                "velocity_per_sprint": 40,
                "net_velocity_per_sprint": 30,
                "sprint_length_weeks": 2,
            }
        )
        html = build_export_html(state)
        assert "Net 30" in html
        assert "Deducted 10" in html
