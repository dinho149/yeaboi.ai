"""Tests for roadmap/export.py — Markdown + HTML export of a RoadmapAnalysis."""

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
    def test_self_contained_and_escaped(self):
        html = build_roadmap_html(_analysis())
        assert "<!DOCTYPE html>" in html
        assert "<style>" in html  # inlined CSS, self-contained
        assert "Billing revamp" in html
        assert "Large" in html and "Small" in html

    def test_escapes_untrusted_project_text(self):
        a = RoadmapAnalysis(
            source_label="R",
            summary="s",
            projects=(RoadmapProject(name="<script>alert(1)</script>", description="<b>x</b>", size="small"),),
        )
        html = build_roadmap_html(a)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


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


class TestSharedDesignSystem:
    def test_roadmap_html_uses_shared_theme(self):
        html = build_roadmap_html(_analysis())
        assert 'data-theme="midnight"' in html
        assert "yeaboi-export-theme" in html  # theme switcher present
        assert 'src="http' not in html and "<link" not in html  # self-contained


class TestVisuals:
    def _analysis(self):
        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject

        return RoadmapAnalysis(
            source_type="file",
            source_label="Q3 plan",
            summary="Two candidates.",
            projects=(
                RoadmapProject(
                    name="SSO rollout",
                    description="Ship SSO for all tenants. Cover break-glass; add audit logs.",
                    size="large",
                    rationale="Security deadline.",
                    quarter="Q3 2026",
                    themes=("Security", "Platform"),
                ),
                RoadmapProject(name="Docs refresh", description="Update onboarding.", size="small", quarter="Q4 2026"),
            ),
            generated_at="2026-07-27T10:00:00+00:00",
        )

    def test_project_cards_with_chips(self):
        from yeaboi.roadmap.export import build_roadmap_html

        html = build_roadmap_html(self._analysis())
        assert html.count("class='card story-card'") == 2
        assert ">Q3 2026</span>" in html
        assert ">Security</span>" in html and ">Platform</span>" in html

    def test_description_split_into_bullets(self):
        from yeaboi.roadmap.export import build_roadmap_html

        html = build_roadmap_html(self._analysis())
        assert "<li>Ship SSO for all tenants.</li>" in html
        assert "<li>Cover break-glass</li>" in html
        assert "<li>add audit logs.</li>" in html

    def test_size_and_quarter_breakdown_bars(self):
        from yeaboi.roadmap.export import build_roadmap_html

        html = build_roadmap_html(self._analysis())
        assert 'class="seg-track"' in html
        assert "Large 1" in html and "Small 1" in html
        assert "Q3 2026 1" in html  # two distinct quarters → by-quarter bar renders

    def test_rationale_badge(self):
        from yeaboi.roadmap.export import build_roadmap_html

        html = build_roadmap_html(self._analysis())
        assert ">Why now</span>" in html
        assert "Security deadline." in html

    def test_hostile_fields_escaped(self):
        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
        from yeaboi.roadmap.export import build_roadmap_html

        html = build_roadmap_html(
            RoadmapAnalysis(projects=(RoadmapProject(name="<b>x</b>", description="<i>d</i>", themes=("<u>t</u>",)),))
        )
        assert "<b>x</b>" not in html and "<i>d</i>" not in html and "<u>t</u>" not in html
