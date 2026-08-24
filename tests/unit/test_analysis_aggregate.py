"""The analysis Go seam (``analysis/aggregate.py``): wire safety, reference fidelity, dispatch."""

from __future__ import annotations

import json

from yeaboi.analysis import aggregate
from yeaboi.team_profile import AiAdoptionSignal


def _items() -> list[dict]:
    return [
        {
            "kind": "commit",
            "title": "add streaming output",
            "body": "Co-Authored-By: Claude <noreply@anthropic.com>",
            "author": "Ada Lovelace",
            "source": "github",
            "matched_members": ["Ada Lovelace"],
            "changed_file_paths": ["src/app.py", "tests/test_app.py"],
        },
        {
            "kind": "pr",
            "title": "fix login redirect",
            "body": "plain human PR",
            "author": "Grace Hopper",
            "source": "github",
            "matched_members": ["Grace Hopper"],
        },
        {
            "kind": "commit",
            "title": "chore: bump deps",
            "body": "",
            "author": "dependabot[bot]",
            "source": "github",
            "matched_members": [],
            "agent_authored": True,
        },
        {"kind": "review", "title": "LGTM", "author": "Ada Lovelace", "source": "github"},
        {"kind": "comment", "title": "question", "author": "Grace Hopper", "source": "github"},
    ]


def _changed_files() -> list[dict]:
    return [
        {
            "provider": "github",
            "container": "acme",
            "repository": "app",
            "path": "src/app.py",
            "status": "succeeded",
            "additions": 40,
            "deletions": 5,
        },
        {
            "provider": "github",
            "container": "acme",
            "repository": "app",
            "path": "src/broken.py",
            "status": "failed",
            "error": "timeout",
        },
    ]


def _classify_inputs() -> dict:
    return aggregate.build_classify_inputs(items=_items())


def _score_inputs(**overrides) -> dict:
    kwargs = dict(
        items=_items(),
        changed_files=_changed_files(),
        selected_users=["Ada Lovelace", "Grace Hopper"],
        window_days=120,
        health_enabled=True,
        changed_file_cache_hits=1,
    )
    kwargs.update(overrides)
    return aggregate.build_score_inputs(**kwargs)


class TestWireSafety:
    def test_classify_inputs_survive_a_json_round_trip(self):
        inputs = _classify_inputs()
        assert json.loads(json.dumps(inputs)) == inputs

    def test_score_inputs_survive_a_json_round_trip(self):
        inputs = _score_inputs()
        assert json.loads(json.dumps(inputs)) == inputs

    def test_results_survive_a_json_round_trip(self):
        classified = aggregate.classify_markers(_classify_inputs())
        scored = aggregate.score_code(_score_inputs())
        assert json.loads(json.dumps(classified)) == classified
        assert json.loads(json.dumps(scored)) == scored

    def test_input_building_copies_rather_than_aliases(self):
        items = _items()
        inputs = aggregate.build_score_inputs(
            items=items,
            changed_files=[],
            selected_users=[],
            window_days=30,
            health_enabled=False,
            changed_file_cache_hits=0,
        )
        items[0]["title"] = "mutated after build"
        assert inputs["items"][0]["title"] == "add streaming output"


class TestReferenceImplementation:
    def test_classify_wraps_the_existing_classifiers_unchanged(self):
        from yeaboi.analysis.ai_usage import _collect_samples, aggregate_ai_markers

        inputs = _classify_inputs()
        result = aggregate.classify_markers(inputs)
        assert result["signal"] == aggregate.signal_to_wire(aggregate_ai_markers(inputs["items"]))
        assert result["samples"] == _collect_samples(inputs["items"], limit=None)

    def test_score_member_activity_orders_by_volume_then_name_and_appends_agents(self):
        result = aggregate.score_code(_score_inputs())
        assert [row["member"] for row in result["member_activity"]] == [
            "Ada Lovelace",
            "Grace Hopper",
            "AI agent accounts",
        ]
        assert result["member_activity"][0] == {"member": "Ada Lovelace", "commits": 1, "prs": 0, "ai_marked": 1}
        # agent_authored routes the row to the agents bucket, but a dependabot
        # commit carries no AI-tool marker, so it counts as activity, not usage.
        assert result["member_activity"][2] == {"member": "AI agent accounts", "commits": 1, "prs": 0, "ai_marked": 0}

    def test_score_health_carries_cache_hits_into_both_summaries(self):
        result = aggregate.score_code(_score_inputs(changed_file_cache_hits=7))
        assert result["health"]["repository_health"]["cached_change_lookups"] == 7
        assert result["health"]["file_coverage"]["cached_change_lookups"] == 7

    def test_health_disabled_returns_empty_scaffold_but_still_scores_practices(self):
        result = aggregate.score_code(_score_inputs(health_enabled=False))
        assert result["health"] == {
            "file_reports": [],
            "findings": [],
            "action_plan": [],
            "file_coverage": {},
            "repository_health": {},
            "coverage_notes": [],
        }
        assert "file_data" in result["practices"]
        assert result["activity_counts"] == {"commits": 2, "prs": 1, "reviews": 1, "comments": 1}

    def test_result_key_order_is_the_wire_contract(self):
        result = aggregate.score_code(_score_inputs())
        assert list(result) == ["member_activity", "practices", "health", "activity_counts"]
        assert list(result["health"]) == [
            "file_reports",
            "findings",
            "action_plan",
            "file_coverage",
            "repository_health",
            "coverage_notes",
        ]


class TestSignalWire:
    def test_round_trip_is_lossless(self):
        from yeaboi.analysis.ai_usage import aggregate_ai_markers

        signal = aggregate_ai_markers(_items())
        assert aggregate.signal_from_wire(aggregate.signal_to_wire(signal)) == signal

    def test_empty_payload_rehydrates_the_default_signal(self):
        assert aggregate.signal_from_wire({}) == AiAdoptionSignal()

    def test_provenance_fields_stay_off_the_wire(self):
        wire = aggregate.signal_to_wire(AiAdoptionSignal(repos_scanned=("GitHub (remote): acme/app",)))
        assert "repos_scanned" not in wire


def _doc_pages() -> list[dict]:
    return [
        {
            "platform": "confluence",
            "container": "PSO",
            "key": "hit",
            "version": "7",
            "title": "Cached runbook",
            "text": "",
            "cache_status": "hit",
            "asset": {
                "title": "Cached runbook",
                "platform": "confluence",
                "clarity": 72.5,
                "usefulness": 80.0,
                "owned": True,
                "actionable": True,
                "structured": True,
                "has_code_blocks": False,
                "marked": False,
                "url": "https://x/wiki/hit",
                "key": "hit",
                "container": "PSO",
                "version": "7",
            },
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "fresh",
            "version": "2026-01-01",
            "title": "Fresh guide",
            "text": "# Purpose\n\nOwner: SRE\n\n- Run the check.\n- Verify the result.",
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "empty",
            "title": "Unreadable",
            "text": "   ",
            "read_error": "empty page body",
        },
    ]


def _docs_inputs(**overrides) -> dict:
    pages = overrides.pop("pages", _doc_pages())
    return aggregate.build_score_docs_inputs(pages=pages)


class TestDocsWireSafety:
    def test_inputs_survive_a_json_round_trip(self):
        inputs = _docs_inputs()
        assert json.loads(json.dumps(inputs)) == inputs

    def test_results_survive_a_json_round_trip(self):
        result = aggregate.score_docs(_docs_inputs())
        assert json.loads(json.dumps(result)) == result

    def test_input_building_copies_rather_than_aliases(self):
        pages = _doc_pages()
        inputs = aggregate.build_score_docs_inputs(pages=pages)
        pages[1]["text"] = "mutated after build"
        assert inputs["pages"][1]["text"].startswith("# Purpose")


class TestDocsReferenceImplementation:
    def test_cached_assets_pass_through_and_fresh_pages_are_scored_in_order(self):
        from yeaboi.analysis.doc_quality import _analyse_page_asset

        inputs = _docs_inputs()
        result = aggregate.score_docs(inputs)
        # The blank-bodied page is dropped; the cached asset crosses untouched.
        assert [asset["key"] for asset in result["assets"]] == ["hit", "fresh"]
        assert result["assets"][0] == inputs["pages"][0]["asset"]
        assert result["assets"][1] == _analyse_page_asset(inputs["pages"][1])

    def test_result_and_summary_key_order_are_the_wire_contract(self):
        result = aggregate.score_docs(_docs_inputs())
        assert list(result) == ["assets", "signal", "summary", "findings", "action_plan", "insights"]
        assert list(result["summary"]) == [
            "pages_scanned",
            "platforms_scanned",
            "avg_clarity",
            "avg_usefulness",
            "clear_pages",
            "mixed_pages",
            "unclear_pages",
            "owned_pages",
            "actionable_pages",
            "structured_pages",
            "ai_marked_pages",
            "per_platform",
            "flagged_pages",
            "is_ai_estimate",
            "small_sample",
        ]
        assert list(result["insights"]) == ["start", "stop", "keep", "try"]

    def test_summary_and_findings_mirror_the_reference_helpers(self):
        from yeaboi.analysis.doc_quality import (
            _aggregate_doc_assets,
            _doc_findings,
            _prioritize_doc_actions,
            doc_small_sample,
        )

        result = aggregate.score_docs(_docs_inputs())
        signal = _aggregate_doc_assets(result["assets"])
        assert result["signal"] == aggregate.doc_signal_to_wire(signal)
        assert result["summary"]["pages_scanned"] == 2
        assert result["summary"]["small_sample"] == doc_small_sample(signal)
        assert result["findings"] == _doc_findings(result["assets"])
        assert result["action_plan"] == _prioritize_doc_actions(result["findings"])

    def test_empty_pages_produce_the_empty_scaffold(self):
        result = aggregate.score_docs(_docs_inputs(pages=[]))
        assert result["assets"] == []
        assert result["signal"]["pages_scanned"] == 0
        assert result["findings"] == []
        assert result["action_plan"] == []
        # Insights still exist (the caller gates them on coverage, not the seam).
        assert set(result["insights"]) == {"start", "stop", "keep", "try"}


class TestDocSignalWire:
    def test_round_trip_is_lossless_including_legacy_fields(self):
        from yeaboi.team_profile import DocQualitySignal

        signal = DocQualitySignal(
            pages_scanned=7,
            platforms_scanned=("confluence", "notion"),
            avg_clarity=61.5,
            avg_usefulness=48.0,
            clear_pages=3,
            mixed_pages=2,
            unclear_pages=2,
            owned_pages=4,
            actionable_pages=5,
            structured_pages=6,
            avg_ai_likelihood=12.5,
            likely_ai_pages=2,
            ai_marked_pages=1,
            per_platform=(("confluence", 5), ("notion", 2)),
            flagged_pages=(("Dense page", "clarity 20/100 — dense or long-winded"),),
            is_ai_estimate=True,
        )
        assert aggregate.doc_signal_from_wire(aggregate.doc_signal_to_wire(signal)) == signal

    def test_wire_key_order_matches_the_dataclass_declaration(self):
        from yeaboi.team_profile import DocQualitySignal

        wire = aggregate.doc_signal_to_wire(DocQualitySignal())
        assert list(wire) == [field for field in DocQualitySignal.__dataclass_fields__]

    def test_empty_payload_rehydrates_the_default_signal(self):
        from yeaboi.team_profile import DocQualitySignal

        assert aggregate.doc_signal_from_wire({}) == DocQualitySignal()
