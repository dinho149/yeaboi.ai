"""The analysis Go seam (``analysis/aggregate.py``): wire safety, reference fidelity, dispatch."""

from __future__ import annotations

import json
import logging

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


class TestGoDispatch:
    """Both analysis seams: Go results win; any failure → Python."""

    def test_no_client_means_python_path(self, monkeypatch):
        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: None)
        assert aggregate.go_classify(_classify_inputs()) is None
        assert aggregate.go_score(_score_inputs()) is None

    def test_client_construction_failure_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("spawn failed")

        monkeypatch.setattr("yeaboi.gocore.get_client", boom)
        assert aggregate.go_classify(_classify_inputs()) is None
        assert aggregate.go_score(_score_inputs()) is None

    def test_core_error_returns_none_for_fallback(self, monkeypatch):
        from yeaboi.gocore import CoreError

        class BrokenClient:
            def request(self, *args, **kwargs):
                raise CoreError("sidecar exploded")

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: BrokenClient())
        assert aggregate.go_classify(_classify_inputs()) is None
        assert aggregate.go_score(_score_inputs()) is None

    def test_malformed_results_return_none(self, monkeypatch):
        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return {"signal": {}} if method == "analysis.classify_markers" else {"member_activity": []}

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        assert aggregate.go_classify(_classify_inputs()) is None
        assert aggregate.go_score(_score_inputs()) is None

    def test_good_results_are_returned_verbatim(self, monkeypatch, caplog):
        canned_classify = aggregate.classify_markers(_classify_inputs())
        canned_score = aggregate.score_code(_score_inputs())

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                assert method in ("analysis.classify_markers", "analysis.score_code")
                return canned_classify if method == "analysis.classify_markers" else canned_score

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        with caplog.at_level(logging.INFO, logger="yeaboi.analysis.aggregate"):
            assert aggregate.go_classify(_classify_inputs()) == canned_classify
            assert aggregate.go_score(_score_inputs()) == canned_score
        assert "analysis.classify_markers served by the sidecar" in caplog.text
        assert "analysis.score_code served by the sidecar" in caplog.text
