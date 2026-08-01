"""Every artifact that can carry annotations actually renders them.

This is the file that stops the worst version of this feature: one where a
reader adds a note, the server accepts it, the store keeps it — and no document
anyone opens ever shows it. The writer believes they corrected the report;
nobody who reads the report learns otherwise.

So the check is not "does the helper work" but "does each exporter call it",
asserted against real output rather than against a call graph.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    Annotation,
    DeliveryReport,
    OneOnOnePrep,
    OneOnOneRecord,
    RetroReport,
    RoadmapAnalysis,
    SixMonthReview,
    StandupReport,
)
from yeaboi.artifacts.render import annotations_markdown, annotations_payload, with_annotations

NOTE = Annotation(kind="note", text="Numbers are low; half the team was on leave.", author="Ada")
FIELD = Annotation(kind="field", label="Risk owner", text="Grace", author="Ada")


def markdown_cases():
    from yeaboi.performance.export import build_completion_markdown, build_prep_markdown, build_review_markdown
    from yeaboi.reporting.export import build_report_markdown
    from yeaboi.retro.export import build_retro_markdown
    from yeaboi.roadmap.export import build_roadmap_markdown
    from yeaboi.standup.export import build_standup_markdown

    return [
        ("standup", build_standup_markdown, StandupReport),
        ("reporting", build_report_markdown, DeliveryReport),
        ("retro", build_retro_markdown, RetroReport),
        ("roadmap", build_roadmap_markdown, RoadmapAnalysis),
        ("prep", build_prep_markdown, OneOnOnePrep),
        ("completion", build_completion_markdown, OneOnOneRecord),
        ("review", build_review_markdown, SixMonthReview),
    ]


def payload_cases():
    from yeaboi.performance.export import completion_export_args, prep_export_args, review_export_args
    from yeaboi.reporting.export import reporting_export_args
    from yeaboi.retro.export import retro_export_args
    from yeaboi.roadmap.export import roadmap_export_args
    from yeaboi.standup.export import standup_export_args

    return [
        ("standup", standup_export_args, StandupReport),
        ("reporting", reporting_export_args, DeliveryReport),
        ("retro", retro_export_args, RetroReport),
        ("roadmap", roadmap_export_args, RoadmapAnalysis),
        ("prep", prep_export_args, OneOnOnePrep),
        ("completion", completion_export_args, OneOnOneRecord),
        ("review", review_export_args, SixMonthReview),
    ]


class TestHelpers:
    def test_payload_carries_text_and_a_discriminator(self):
        (row,) = annotations_payload([NOTE])
        assert row["kind"] == "note"
        assert row["text"] == NOTE.text
        # No presentation over the wire: nothing here names a colour or a class.
        assert set(row) == {"kind", "anchor", "label", "text", "author", "avatar", "at"}

    def test_empty_annotations_render_nothing(self):
        assert annotations_payload([]) == []
        assert annotations_markdown([]) == []

    def test_a_blank_annotation_is_dropped(self):
        assert annotations_payload([Annotation(text="")]) == []

    def test_markdown_names_a_field_and_its_author(self):
        out = "\n".join(annotations_markdown([FIELD]))
        assert "**Risk owner:** Grace" in out
        assert "_Ada_" in out

    def test_markdown_shows_where_an_anchored_note_belongs(self):
        anchored = Annotation(text="was on call", anchor="member_updates[name=Ada]")
        assert "member_updates[name=Ada]" in "\n".join(annotations_markdown([anchored]))

    def test_with_annotations_omits_the_key_when_there_are_none(self):
        args = with_annotations({"report": {"kind": "standup"}}, StandupReport())
        assert "annotations" not in args["report"]

    def test_with_annotations_tolerates_an_artifact_without_the_field(self):
        args = with_annotations({"report": {}}, object())
        assert args["report"] == {}


@pytest.mark.parametrize("name,build,cls", markdown_cases(), ids=lambda v: v if isinstance(v, str) else "")
class TestEveryMarkdownExporterRendersThem:
    def test_a_note_reaches_the_markdown(self, name, build, cls):
        out = build(cls(annotations=(NOTE,)))
        assert NOTE.text in out, f"{name} markdown drops annotations"

    def test_a_named_field_reaches_the_markdown(self, name, build, cls):
        out = build(cls(annotations=(FIELD,)))
        assert "Risk owner" in out and "Grace" in out, f"{name} markdown drops added fields"

    def test_an_unannotated_document_gains_no_heading(self, name, build, cls):
        from yeaboi.artifacts.render import NOTES_HEADING

        assert NOTES_HEADING not in build(cls())


@pytest.mark.parametrize("name,args_for,cls", payload_cases(), ids=lambda v: v if isinstance(v, str) else "")
class TestEveryPayloadCarriesThem:
    def test_annotations_reach_the_payload(self, name, args_for, cls):
        report = args_for(cls(annotations=(NOTE, FIELD)))["report"]
        assert "annotations" in report, f"{name} payload drops annotations"
        assert [a["text"] for a in report["annotations"]] == [NOTE.text, FIELD.text]

    def test_the_key_is_absent_when_there_are_none(self, name, args_for, cls):
        # Absent rather than empty, matching build_chrome — and it is what keeps
        # every committed wire fixture byte-identical for an unannotated export.
        assert "annotations" not in args_for(cls())["report"]


class TestTheTwoRenderersAgree:
    """The Markdown and the HTML have to call the section the same thing.

    They are produced by different languages from different files, and a reader
    comparing an emailed .md against the shared page should not have to wonder
    whether "Added by the team" and whatever the bundle says are the same list.
    """

    def test_the_component_heading_matches_the_python_constant(self):
        from pathlib import Path

        from yeaboi.artifacts.render import NOTES_HEADING

        source = Path(__file__).resolve().parents[2] / "frontend/src/export/reports/Annotations.tsx"
        if not source.is_file():
            import pytest as _pytest

            _pytest.skip("frontend/ is not present in this checkout")
        assert f"const HEADING = '{NOTES_HEADING}'" in source.read_text(encoding="utf-8")


class TestEveryEditableModeOffersItsPaths:
    """The payload half of the round trip, for each mode that has one.

    Standup has an end-to-end test of its own; this is the breadth check —
    that every other correctable report actually hands the browser somewhere to
    write, and that no file export does.
    """

    def cases(self):
        from yeaboi.agent.state import (
            DeliveredItem,
            DeliveryReport,
            OneOnOnePrep,
            RetroCard,
            RetroReport,
            RoadmapAnalysis,
            RoadmapProject,
        )
        from yeaboi.performance.export import prep_export_args
        from yeaboi.reporting.export import reporting_export_args
        from yeaboi.retro.export import retro_export_args
        from yeaboi.roadmap.export import roadmap_export_args

        return [
            (
                "reporting",
                reporting_export_args,
                DeliveryReport(headline="Good", delivered_items=(DeliveredItem(key="YB-1", title="Thing"),)),
                "headline",
            ),
            (
                "roadmap",
                roadmap_export_args,
                RoadmapAnalysis(summary="s", projects=(RoadmapProject(name="Payments", description="d"),)),
                "summary",
            ),
            ("prep", prep_export_args, OneOnOnePrep(engineer="Ada", activity_summary="x"), "activity_summary"),
            (
                "retro",
                retro_export_args,
                RetroReport(cards=(RetroCard(id="c1", grid="went_well", text="good"),)),
                None,
            ),
        ]

    def test_a_served_document_carries_paths_and_raw_values(self):
        for name, args_for, artifact, field in self.cases():
            payload = args_for(artifact, editable=True)["report"]
            if field is None:
                target = payload["columns"][0]["cards"][0]["edit"]["text"]
            else:
                target = payload["edit"][field]
            assert target["path"], f"{name} sent no path"
            assert isinstance(target["value"], str), f"{name} sent no raw value"

    def test_a_file_export_carries_none_of_it(self):
        import json

        for name, args_for, artifact, _ in self.cases():
            assert '"edit"' not in json.dumps(args_for(artifact)["report"]), f"{name} file export leaked edit paths"

    def test_a_row_path_is_one_the_server_accepts(self):
        from yeaboi.agent.state import RetroCard, RetroReport
        from yeaboi.artifacts.edits import Edit, apply_edits
        from yeaboi.artifacts.registry import ARTIFACTS
        from yeaboi.retro.export import retro_export_args

        artifact = RetroReport(cards=(RetroCard(id="c1", grid="went_well", text="good"),))
        path = retro_export_args(artifact, editable=True)["report"]["columns"][0]["cards"][0]["edit"]["text"]["path"]
        out, results = apply_edits(
            artifact, (Edit(edit_id="e1", op="set", path=path, value="great"),), ARTIFACTS["retro"]
        )
        assert results[0].applied and out.cards[0].text == "great"
