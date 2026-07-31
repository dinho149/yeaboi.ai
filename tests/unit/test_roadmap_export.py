"""Tests for roadmap/export.py — Markdown + HTML export of a RoadmapAnalysis."""

from tests._pages import island
from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
from yeaboi.roadmap.export import (
    _slug,
    build_roadmap_html,
    build_roadmap_markdown,
    export_roadmap,
)


def _analysis(with_projects: bool = True, with_warnings: bool = True) -> RoadmapAnalysis:
    projects = (
        (
            RoadmapProject(
                name="Billing revamp",
                description="Rebuild the billing engine to support metered plans.",
                size="large",
                rationale="Revenue-critical; unblocks upsell.",
                priority=1,
                themes=("Payments", "Growth"),
                quarter="Q3 2026",
            ),
            RoadmapProject(
                name="SSO login",
                description="Add SAML/OIDC single sign-on.",
                size="small",
                rationale="Frequent customer ask.",
                priority=2,
                themes=("Security",),
                quarter="Q3 2026",
            ),
        )
        if with_projects
        else ()
    )
    return RoadmapAnalysis(
        source_type="local",
        source_locator="/tmp/q3-2026-roadmap.md",
        source_label="Q3 2026 Roadmap",
        summary="The quarter focuses on revenue and security.",
        projects=projects,
        warnings=("Roadmap truncated at 24,000 characters",) if with_warnings else (),
        generated_at="2026-07-20T09:00:00",
    )


class TestMarkdown:
    def test_contains_title_summary_projects(self):
        md = build_roadmap_markdown(_analysis())
        assert "# Roadmap — Q3 2026 Roadmap" in md
        assert "The quarter focuses on revenue and security." in md
        assert "### 1. Billing revamp  ·  Large" in md
        assert "### 2. SSO login  ·  Small" in md
        assert "Rebuild the billing engine" in md  # full description
        assert "**Why now:** Revenue-critical" in md

    def test_meta_and_notices(self):
        md = build_roadmap_markdown(_analysis())
        assert "Q3 2026 · Payments, Growth" in md
        assert "## ⚠ Notices" in md
        assert "Roadmap truncated" in md

    def test_no_projects_message(self):
        md = build_roadmap_markdown(_analysis(with_projects=False))
        assert "No projects were extracted" in md

    def test_no_warnings_omits_notices(self):
        md = build_roadmap_markdown(_analysis(with_warnings=False))
        assert "Notices" not in md


class TestHtml:
    """The page is a payload plus a bundle, so the assertions are about the payload.

    How a large project gets an amber chip, or a description becomes bullets, is
    the bundle's business and is tested in `frontend/src/export/reports/*.test.tsx`.
    What is tested here is that every field the renderer needs arrives, in the
    shape it expects — the direction that fails *silently*, since a dropped key
    is just a section that never draws.
    """

    def test_self_contained(self):
        html = build_roadmap_html(_analysis())
        assert "<!DOCTYPE html>" in html
        assert "<style>" in html  # inlined CSS, self-contained
        assert 'data-mode="planning"' in html

    def test_payload_carries_every_project_field(self):
        report = island(build_roadmap_html(_analysis()))["report"]
        assert report["kind"] == "roadmap"
        assert report["summary"] == "The quarter focuses on revenue and security."
        assert [p["name"] for p in report["projects"]] == ["Billing revamp", "SSO login"]
        first = report["projects"][0]
        assert first == {
            "index": 1,
            "name": "Billing revamp",
            "size": "large",
            "quarter": "Q3 2026",
            "themes": ["Payments", "Growth"],
            "description": "Rebuild the billing engine to support metered plans.",
            "rationale": "Revenue-critical; unblocks upsell.",
        }
        assert report["warnings"] == ["Roadmap truncated at 24,000 characters"]

    def test_index_falls_back_to_position_when_unprioritised(self):
        analysis = RoadmapAnalysis(projects=(RoadmapProject(name="A"), RoadmapProject(name="B")))
        report = island(build_roadmap_html(analysis))["report"]
        assert [p["index"] for p in report["projects"]] == [1, 2]

    def test_untrusted_project_text_stays_inert_data(self):
        # The old page interpolated these into markup, so the test asked for the
        # escaped form. Now they never touch the HTML parser: they are string
        # values in a `type="application/json"` element, which is not executable,
        # and json_island escapes the three characters that would otherwise end
        # it early. So the assertion is that nothing reached the document as
        # markup — and that the value survived intact for the renderer.
        analysis = RoadmapAnalysis(
            source_label="R",
            summary="s",
            projects=(RoadmapProject(name="<script>alert(1)</script>", description="<b>x</b>", size="small"),),
        )
        html = build_roadmap_html(analysis)
        assert "<script>alert(1)</script>" not in html
        assert "<b>x</b>" not in html
        project = island(html)["report"]["projects"][0]
        assert project["name"] == "<script>alert(1)</script>"
        assert project["description"] == "<b>x</b>"


class TestExportWrite:
    def test_writes_markdown_and_html(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "ROADMAP_EXPORTS_DIR", tmp_path / "roadmap")
        out = export_roadmap(_analysis(), name="Q3 2026 Roadmap")
        assert out["markdown"].exists() and out["markdown"].suffix == ".md"
        assert out["html"].exists() and out["html"].suffix == ".html"
        assert "Billing revamp" in out["markdown"].read_text()
        # Sub-directory is slugged from the friendly name.
        assert out["markdown"].parent.name == "q3-2026-roadmap"

    def test_reexport_overwrites(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "ROADMAP_EXPORTS_DIR", tmp_path / "roadmap")
        first = export_roadmap(_analysis(), name="R")
        second = export_roadmap(_analysis(), name="R")
        assert first["markdown"] == second["markdown"]  # same path — latest wins


class TestSlug:
    def test_slug_basic(self):
        assert _slug("Q3 2026 Roadmap") == "q3-2026-roadmap"
        assert _slug("") == "roadmap"


class TestChrome:
    def test_header_facts_name_the_source_and_the_run(self):
        chrome = island(build_roadmap_html(_analysis()))["chrome"]
        assert chrome["wordmark"] == "roadmap"
        assert chrome["frame"] == "yeaboi — planning"
        assert chrome["facts"] == [
            ["SOURCE", "local"],
            ["FROM", "/tmp/q3-2026-roadmap.md"],
            ["ANALYZED", "2026-07-20"],
        ]

    def test_empty_facts_are_dropped_rather_than_shown_blank(self):
        chrome = island(build_roadmap_html(RoadmapAnalysis(source_label="R")))["chrome"]
        assert chrome.get("facts", []) == []

    def test_noscript_names_the_sibling_markdown(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "ROADMAP_EXPORTS_DIR", tmp_path / "roadmap")
        out = export_roadmap(_analysis(), name="R")
        # The page draws client-side, so the no-JavaScript answer is the file
        # written next to it — which is only useful if the page says its name.
        assert f"<code>{out['markdown'].name}</code>" in out["html"].read_text()
