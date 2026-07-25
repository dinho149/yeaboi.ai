"""Tests for the AI-adoption sub-analysis (analysis/ai_usage.py) and its wiring.

Covers: the marker classifier (one case per tool + no-match + multi-tool suppression),
the pure aggregation, the deterministic fallback coaching, the LLM insights path
(mocked, happy + fallback), the graceful data-gathering fan-out, the round-trip of the
new AiAdoptionSignal through the profile store, the enriched local-git body capture,
and the TUI card builder (populated + empty state).
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import asdict

from yeaboi.analysis.ai_usage import (
    _classify_ai_markers,
    _fallback_ai_adoption_insights,
    aggregate_ai_markers,
    collect_ai_activity,
    generate_ai_adoption_insights,
    run_ai_adoption,
)
from yeaboi.team_profile import AiAdoptionSignal, TeamProfile, _dict_to_profile
from yeaboi.tools.local_git import git_subprocess_env, local_git_recent_commits

# ── Classifier ─────────────────────────────────────────────────────────────


class TestClassifyAiMarkers:
    def test_claude_co_author(self):
        assert _classify_ai_markers("feat\n\nCo-Authored-By: Claude <noreply@anthropic.com>") == {"claude"}

    def test_claude_generated_with(self):
        assert _classify_ai_markers("🤖 Generated with [Claude Code](https://claude.com/claude-code)") == {"claude"}

    def test_copilot(self):
        assert _classify_ai_markers("Co-authored-by: Copilot <copilot@github.com>") == {"copilot"}

    def test_cursor(self):
        assert _classify_ai_markers("Co-authored-by: Cursor Agent <agent@cursor.com>") == {"cursor"}

    def test_aider(self):
        assert _classify_ai_markers("aider: refactor module\n\nCo-authored-by: aider (gpt-4)") == {"aider"}

    def test_devin(self):
        assert _classify_ai_markers("work by devin-ai") == {"devin"}

    def test_codeium(self):
        assert _classify_ai_markers("edited with Windsurf") == {"codeium"}

    def test_generic_other_ai(self):
        assert _classify_ai_markers("Co-Authored-By: Some Assistant <bot@x.com>") == {"other_ai"}

    def test_no_match(self):
        assert _classify_ai_markers("just a normal commit message") == set()

    def test_empty(self):
        assert _classify_ai_markers("") == set()
        assert _classify_ai_markers(None) == set()  # type: ignore[arg-type]

    def test_specific_suppresses_other_ai(self):
        # A Claude commit that also carries a generic AI trailer is credited to claude only.
        result = _classify_ai_markers("Co-Authored-By: Claude\nCo-Authored-By: some bot")
        assert result == {"claude"}


# ── Aggregation ────────────────────────────────────────────────────────────


class TestAggregate:
    def test_buckets_and_footprint(self):
        items = [
            {"kind": "commit", "author": "Alice", "title": "feat", "body": "Co-Authored-By: Claude", "source": "gh"},
            {"kind": "commit", "author": "Bob", "title": "docs: update README", "body": "", "source": "local_git"},
            {
                "kind": "pr",
                "author": "Alice",
                "title": "Add feature",
                "body": "Generated with Claude Code",
                "source": "gh",
            },
        ]
        sig = aggregate_ai_markers(items)
        assert sig.scanned_commits == 2
        assert sig.scanned_prs == 1
        assert sig.ai_commits == 1
        assert sig.ai_prs == 1
        assert sig.footprint_pct == round(2 / 3 * 100, 1)
        assert sig.per_tool == (("claude", 2),)
        assert sig.per_author == (("Alice", 2),)
        assert dict(sig.per_activity) == {"code": 1, "pr": 1}
        assert dict(sig.per_source) == {"gh": 2}  # both AI-marked items came from the gh source
        assert sig.is_lower_bound is True

    def test_docs_bucket(self):
        items = [
            {"kind": "commit", "author": "A", "title": "docs: add guide.md", "body": "Co-Authored-By: Claude"},
        ]
        sig = aggregate_ai_markers(items)
        assert dict(sig.per_activity) == {"docs": 1}

    def test_empty(self):
        sig = aggregate_ai_markers([])
        assert sig.scanned_commits == 0 and sig.footprint_pct == 0.0
        assert sig.per_tool == () and sig.per_author == ()

    def test_non_commit_pr_kinds_ignored(self):
        sig = aggregate_ai_markers([{"kind": "page", "title": "x", "body": "Co-Authored-By: Claude"}])
        assert sig.scanned_commits == 0 and sig.scanned_prs == 0


# ── Fallback coaching ──────────────────────────────────────────────────────


class TestFallbackInsights:
    def test_all_categories_non_empty_low_footprint(self):
        sig = AiAdoptionSignal(
            scanned_commits=10,
            ai_commits=1,
            footprint_pct=10.0,
            per_tool=(("claude", 1),),
            per_author=(("A", 1),),
            per_activity=(("code", 1),),
        )
        fb = _fallback_ai_adoption_insights(sig)
        for cat in ("start", "stop", "keep", "try"):
            assert fb[cat], f"category {cat} is empty"

    def test_all_categories_non_empty_empty_signal(self):
        fb = _fallback_ai_adoption_insights(AiAdoptionSignal())
        for cat in ("start", "stop", "keep", "try"):
            assert fb[cat]

    def test_other_ai_triggers_stop(self):
        sig = AiAdoptionSignal(scanned_commits=4, ai_commits=2, footprint_pct=50.0, per_tool=(("other_ai", 2),))
        fb = _fallback_ai_adoption_insights(sig)
        titles = " ".join(it["title"].lower() for it in fb["stop"])
        assert "unlabelled" in titles or "trailer" in titles

    def test_cites_sample_with_link(self):
        # Commits-with-AI but no PRs → the "draft PR descriptions" item cites a real commit + link.
        sig = AiAdoptionSignal(
            scanned_commits=5, ai_commits=3, footprint_pct=60.0, per_tool=(("claude", 3),), per_activity=(("code", 3),)
        )
        samples = [
            {
                "author": "Dinho",
                "tool": "claude",
                "activity": "code",
                "title": "Fix login",
                "source": "local_git",
                "key": "a1b2c3d4",
                "url": "https://github.com/o/r/commit/a1b2c3d4",
            }
        ]
        fb = _fallback_ai_adoption_insights(sig, samples)
        pr_item = next(it for it in fb["start"] if "PR" in it["title"])
        assert "a1b2c3d4" in pr_item["evidence"] and "Fix login" in pr_item["evidence"]
        assert pr_item["link"] == "https://github.com/o/r/commit/a1b2c3d4"


# ── LLM insights (mocked) ──────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, content):
        self.content = content


class TestGenerateInsights:
    _SIG = AiAdoptionSignal(
        scanned_commits=10,
        scanned_prs=2,
        ai_commits=4,
        ai_prs=1,
        footprint_pct=41.7,
        per_tool=(("claude", 5),),
        per_author=(("A", 5),),
        per_activity=(("code", 4), ("pr", 1)),
        sources_scanned=("github",),
    )

    def test_happy_path_parses_json(self, monkeypatch):
        payload = (
            '{"start": [{"title": "Do X", "detail": "d", "evidence": "e"}], '
            '"stop": [{"title": "Stop Y", "detail": "d", "evidence": "e"}], '
            '"keep": [{"title": "Keep Z", "detail": "d", "evidence": "e"}], '
            '"try": [{"title": "Try W", "detail": "d", "evidence": "e"}]}'
        )
        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", lambda *a, **k: _FakeResp(payload))
        out = generate_ai_adoption_insights(self._SIG, {})
        assert out["start"][0]["title"] == "Do X"
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_uses_analysis_fast_model(self, monkeypatch):
        payload = (
            '{"start": [{"title": "S"}], "stop": [{"title": "X"}], "keep": [{"title": "K"}], "try": [{"title": "T"}]}'
        )
        captured = {}

        def _invoke(*args, **kwargs):
            captured.update(kwargs)
            return _FakeResp(payload)

        monkeypatch.setattr("yeaboi.agent.llm.get_analysis_fast_model", lambda: "fast-model")
        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", _invoke)
        generate_ai_adoption_insights(self._SIG, {})
        assert captured["model"] == "fast-model"

    def test_llm_failure_falls_back(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no llm")

        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", boom)
        out = generate_ai_adoption_insights(self._SIG, {})
        # Falls back deterministically, never raises, every category populated.
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_link_validated_against_samples(self, monkeypatch):
        # A real sample URL is kept; a hallucinated one is dropped.
        good = "https://github.com/o/r/commit/deadbeef"
        payload = (
            f'{{"start": [{{"title": "Real", "detail": "d", "evidence": "e", "link": "{good}"}}], '
            '"stop": [{"title": "Fake", "detail": "d", "evidence": "e", "link": "https://evil.example/x"}], '
            '"keep": [{"title": "K", "detail": "d", "evidence": "e"}], '
            '"try": [{"title": "T", "detail": "d", "evidence": "e"}]}'
        )
        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", lambda *a, **k: _FakeResp(payload))
        examples = {"samples": [{"url": good, "title": "x", "key": "deadbeef", "tool": "claude", "activity": "code"}]}
        out = generate_ai_adoption_insights(self._SIG, examples)
        assert out["start"][0]["link"] == good  # valid link kept
        assert "link" not in out["stop"][0]  # hallucinated link dropped


# ── Data gathering (graceful fan-out) ──────────────────────────────────────


class TestCollectAiActivity:
    def test_no_config_records_coverage_gaps(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: None)
        items, sources, coverage, repos = collect_ai_activity("jira", "PROJ")
        assert items == [] and sources == [] and repos == []
        assert any("github" in c for c in coverage)
        assert any("azdo" in c for c in coverage)
        # Local scanning was removed — never reported as a source or a coverage gap.
        assert not any("local" in c for c in coverage)

    def test_github_items_tagged(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "o/r")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: None)
        monkeypatch.setattr(
            "yeaboi.tools.github.github_recent_commits",
            lambda repo, days=1: [{"kind": "commit", "author": "A", "title": "x", "body": ""}],
        )
        monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, days=1: [])
        items, sources, _, repos = collect_ai_activity("jira", "PROJ")
        assert sources == ["github"]
        assert items and items[0]["source"] == "github"
        assert repos == ["GitHub (remote): o/r"]

    def test_sub_sources_restricts_to_azdo_only(self, monkeypatch):
        # GitHub is configured but not requested → skipped; only Azure Repos scanned.
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "o/r")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Proj")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "pat")

        def _boom(*a, **k):
            raise AssertionError("GitHub must not be scanned when sub_sources=['azdo']")

        monkeypatch.setattr("yeaboi.tools.github.github_recent_commits", _boom)
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_recent_commits",
            lambda proj, days=1: [{"kind": "commit", "author": "A", "title": "x", "body": ""}],
        )
        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_recent_prs", lambda proj, days=1: [])
        items, sources, _, repos = collect_ai_activity("jira", "PROJ", sub_sources=["azdo"])
        assert sources == ["azdo"]

    def test_source_error_recorded_not_raised(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "o/r")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: None)

        def boom(repo, days=1):
            raise RuntimeError("boom")

        monkeypatch.setattr("yeaboi.tools.github.github_recent_commits", boom)
        monkeypatch.setattr("yeaboi.tools.github.github_recent_prs", lambda repo, days=1: [])
        items, sources, coverage, repos = collect_ai_activity("jira", "PROJ")
        assert items == [] and sources == [] and repos == []
        assert any("github" in c and "error" in c for c in coverage)


class TestRunAiAdoption:
    def test_aggregates_collected_items(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda source, project, sub_sources=None: (
                [
                    {
                        "kind": "commit",
                        "author": "A",
                        "title": "x",
                        "body": "Co-Authored-By: Claude",
                        "source": "github",
                        "container": "o",
                        "repository": "o/r",
                        "key": "a1b2c3d4",
                        "url": "https://github.com/o/r/commit/a1b2c3d4",
                    }
                ],
                ["github"],
                [],
                ["GitHub (remote): o/r"],
            ),
        )
        sig, blob = run_ai_adoption("jira", "P", [], [], members=["A"])
        assert sig.ai_commits == 1 and sig.footprint_pct == 100.0
        assert sig.sources_scanned == ("github",)
        assert sig.repos_scanned == ("GitHub (remote): o/r",)
        assert sig.per_source == (("github", 1),)
        assert blob["summary"]["ai_commits"] == 1
        assert blob["summary"]["per_source"] == [["github", 1]]
        # Samples carry a ref (key) and link so examples are inspectable.
        assert blob["samples"] and blob["samples"][0]["tool"] == "claude"
        assert blob["samples"][0]["key"] == "a1b2c3d4"
        assert blob["samples"][0]["url"].endswith("/commit/a1b2c3d4")

    def test_collect_failure_returns_empty_signal(self, monkeypatch):
        def boom(source, project, sub_sources=None):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.analysis.ai_usage.collect_ai_activity", boom)
        sig, blob = run_ai_adoption("jira", "P", [], [])
        assert sig == AiAdoptionSignal()
        assert "coverage" in blob

    def _two_author_items(self):
        return (
            [
                {
                    "kind": "commit",
                    "author": "Alice",
                    "title": "x",
                    "body": "Co-Authored-By: Claude",
                    "source": "github",
                },
                {"kind": "commit", "author": "Bob", "title": "y", "body": "Co-Authored-By: Claude", "source": "github"},
            ],
            ["github"],
            [],
            ["GitHub (remote): o/r"],
        )

    def test_member_filter_rescopes_to_selected_author(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity", lambda s, p, sub_sources=None: self._two_author_items()
        )
        sig, _ = run_ai_adoption("jira", "P", [], [], members=["Alice"])
        # Only Alice's commit is counted.
        assert sig.scanned_commits == 1
        assert dict(sig.per_author) == {"Alice": 1}

    def test_member_filter_email_localpart_match(self, monkeypatch):
        items = (
            [
                {
                    "kind": "commit",
                    "author": "asmith",
                    "author_email": "alice@x.com",
                    "title": "x",
                    "body": "",
                    "source": "github",
                }
            ],
            ["github"],
            [],
            [],
        )
        monkeypatch.setattr("yeaboi.analysis.ai_usage.collect_ai_activity", lambda s, p, sub_sources=None: items)
        sig, _ = run_ai_adoption("jira", "P", [], [], members=["alice"])
        assert sig.scanned_commits == 1  # matched via email local-part

    def test_member_filter_no_match_stays_empty(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity", lambda s, p, sub_sources=None: self._two_author_items()
        )
        sig, blob = run_ai_adoption("jira", "P", [], [], members=["Nobody"])
        assert sig.scanned_commits == 0
        assert blob["unmatched_users"] == ["Nobody"]
        assert any("no commits or authored PRs matched" in c for c in blob["coverage"])

    def test_changed_files_are_fetched_only_after_member_filter(self, monkeypatch):
        items = [
            {
                "kind": "commit",
                "author": "Alice",
                "source": "github",
                "container": "acme",
                "repository": "acme/api",
                "commit_id": "alice-sha",
                "title": "Selected change",
                "body": "",
            },
            {
                "kind": "commit",
                "author": "Bob",
                "source": "github",
                "container": "acme",
                "repository": "acme/api",
                "commit_id": "bob-sha",
                "title": "Unselected change",
                "body": "",
            },
        ]
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                items,
                ["github"],
                [],
                ["GitHub (remote): acme/api"],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        captured = {}

        def changed(repo, activity):
            captured["ids"] = [item["commit_id"] for item in activity]
            return [
                {
                    "provider": "github",
                    "container": "acme",
                    "repository": repo,
                    "path": "src/api.py",
                    "status": "modified",
                    "attribution": "authored_commit",
                    "confidence": "high",
                    "change_id": "alice-sha",
                }
            ]

        monkeypatch.setattr("yeaboi.tools.github.github_changed_files", changed)

        sig, blob = run_ai_adoption("jira", "P", [], [], members=["Alice"])

        assert sig.scanned_commits == 1
        assert captured["ids"] == ["alice-sha"]
        assert [item["path"] for item in blob["changed_files"]] == ["src/api.py"]

    def test_ai_footprint_only_skips_changed_file_fetch(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                [
                    {
                        "kind": "commit",
                        "author": "Alice",
                        "source": "github",
                        "container": "acme",
                        "repository": "acme/api",
                        "commit_id": "sha",
                        "title": "AI change",
                        "body": "Co-Authored-By: Claude",
                    }
                ],
                ["github"],
                [],
                [],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        monkeypatch.setattr(
            "yeaboi.tools.github.github_changed_files",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch files")),
        )
        signal, blob = run_ai_adoption("jira", "P", [], [], members=["Alice"], code_features=["ai_footprint"])
        assert signal.ai_commits == 1
        assert blob["enabled_features"] == ["ai_footprint"]
        assert blob["changed_files"] == []
        assert blob["repository_health"] == {}

    def test_code_health_only_skips_marker_output(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                [],
                ["github"],
                [],
                [],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        signal, blob = run_ai_adoption("jira", "P", [], [], members=["Alice"], code_features=["code_health"])
        assert signal == AiAdoptionSignal()
        assert blob["enabled_features"] == ["code_health"]
        assert blob["samples"] == []
        assert "repository_health" in blob

    def test_changed_file_metadata_is_bounded_parallel_and_reports_counts(self, monkeypatch):
        items = [
            {
                "kind": "commit",
                "author": "Alice",
                "source": "github",
                "container": "acme",
                "repository": "acme/api",
                "commit_id": f"sha-{index}",
                "title": f"Change {index}",
                "body": "",
            }
            for index in range(4)
        ]
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                items,
                ["github"],
                [],
                ["GitHub (remote): acme/api"],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        monkeypatch.setattr("yeaboi.config.get_team_analysis_code_max_concurrency", lambda: 2)
        active = 0
        maximum = 0
        lock = threading.Lock()

        def changed(repo, activity):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            item = activity[0]
            return [
                {
                    "provider": "github",
                    "container": "acme",
                    "repository": repo,
                    "path": f"src/{item['commit_id']}.py",
                    "status": "modified",
                    "attribution": "authored_commit",
                    "confidence": "high",
                    "change_id": item["commit_id"],
                }
            ]

        monkeypatch.setattr("yeaboi.tools.github.github_changed_files", changed)
        progress = []

        _signal, blob = run_ai_adoption(
            "jira",
            "P",
            [],
            [],
            members=["Alice"],
            progress=progress,
            code_features=["code_health"],
        )

        assert maximum == 2
        assert [item["path"] for item in blob["changed_files"]] == [f"src/sha-{index}.py" for index in range(4)]
        updates = [
            event
            for event in progress
            if isinstance(event, dict)
            and event.get("component_id") == "code:code_health"
            and event.get("phase") == "Reading code-change metadata"
        ]
        assert updates[-1]["current"] == updates[-1]["total"] == 4
        assert updates[-1]["secondary_count"] == 4
        assert updates[-1]["read_only"] is True

    def test_change_lookup_cap_prefers_newest_and_discloses(self, monkeypatch):
        items = [
            {
                "kind": "commit",
                "author": "Alice",
                "source": "github",
                "container": "acme",
                "repository": "acme/api",
                "commit_id": f"sha-{index}",
                "title": f"Change {index}",
                "body": "",
                "timestamp": f"2026-01-0{index + 1}T00:00:00",
            }
            for index in range(4)
        ]
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                items,
                ["github"],
                [],
                ["GitHub (remote): acme/api"],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        monkeypatch.setattr("yeaboi.config.get_team_analysis_max_change_lookups", lambda: 2)
        fetched: list[str] = []

        def changed(repo, activity):
            fetched.append(activity[0]["commit_id"])
            return []

        monkeypatch.setattr("yeaboi.tools.github.github_changed_files", changed)

        _signal, blob = run_ai_adoption("jira", "P", [], [], members=["Alice"], code_features=["code_health"])

        assert sorted(fetched) == ["sha-2", "sha-3"]  # the two newest; older ones skipped
        assert any("capped at 2 of 4" in note for note in blob["coverage"])

    def test_immutable_commit_file_metadata_is_reused(self, monkeypatch, tmp_path):
        item = {
            "kind": "commit",
            "author": "Alice",
            "source": "github",
            "container": "acme",
            "repository": "acme/api",
            "commit_id": "immutable-sha",
            "title": "Change",
            "body": "",
        }
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                [item],
                ["github"],
                [],
                ["GitHub (remote): acme/api"],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        calls = 0

        def changed(repo, activity):
            nonlocal calls
            calls += 1
            return [
                {
                    "provider": "github",
                    "container": "acme",
                    "repository": repo,
                    "path": "src/api.py",
                    "status": "modified",
                    "attribution": "authored_commit",
                    "confidence": "high",
                    "change_id": activity[0]["commit_id"],
                }
            ]

        monkeypatch.setattr("yeaboi.tools.github.github_changed_files", changed)
        db_path = tmp_path / "analysis.db"

        first = run_ai_adoption("jira", "P", [], [], members=["Alice"], code_features=["code_health"], db_path=db_path)[
            1
        ]
        second = run_ai_adoption(
            "jira", "P", [], [], members=["Alice"], code_features=["code_health"], db_path=db_path
        )[1]

        assert calls == 1
        assert first["changed_files"][0]["path"] == second["changed_files"][0]["path"]
        assert second["changed_files"][0]["cache_status"] == "hit"
        assert second["repository_health"]["cached_change_lookups"] == 1

    def test_recommendations_overlap_code_change_collection(self, monkeypatch):
        item = {
            "kind": "commit",
            "author": "Alice",
            "source": "github",
            "container": "acme",
            "repository": "acme/api",
            "commit_id": "sha",
            "title": "AI change",
            "body": "Co-Authored-By: Claude",
        }
        monkeypatch.setattr(
            "yeaboi.analysis.ai_usage.collect_ai_activity",
            lambda *args, **kwargs: (
                [item],
                ["github"],
                [],
                ["GitHub (remote): acme/api"],
                [],
                {"component": "code", "status": "complete", "assets": []},
            ),
        )
        insight_started = threading.Event()
        change_started = threading.Event()
        overlapped = []

        def insights(*args, **kwargs):
            insight_started.set()
            overlapped.append(change_started.wait(timeout=1))
            return {"start": [], "stop": [], "keep": [], "try": []}

        def changed(repo, activity):
            change_started.set()
            overlapped.append(insight_started.wait(timeout=1))
            return []

        monkeypatch.setattr("yeaboi.analysis.ai_usage.generate_ai_adoption_insights", insights)
        monkeypatch.setattr("yeaboi.tools.github.github_changed_files", changed)

        _signal, blob = run_ai_adoption(
            "jira",
            "P",
            [],
            [],
            members=["Alice"],
            code_features=["ai_footprint", "code_health"],
            generate_insights=True,
        )

        assert overlapped == [True, True]
        assert "insights" in blob


# ── Serialization round-trip ───────────────────────────────────────────────


class TestSerialization:
    def test_profile_roundtrip_preserves_signal(self):
        sig = AiAdoptionSignal(
            scanned_commits=5,
            scanned_prs=2,
            ai_commits=3,
            ai_prs=1,
            footprint_pct=57.1,
            per_tool=(("claude", 3), ("copilot", 1)),
            per_author=(("A", 3), ("B", 1)),
            per_activity=(("code", 3), ("pr", 1)),
            per_source=(("local_git", 3), ("github", 1)),
            repos_scanned=("Local clone: /repo", "GitHub (remote): o/r"),
            sources_scanned=("local_git", "github"),
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        restored = _dict_to_profile(asdict(profile))
        assert restored.ai_adoption == sig
        assert isinstance(restored.ai_adoption, AiAdoptionSignal)

    def test_old_profile_without_key_defaults(self):
        d = {"team_id": "t", "source": "jira", "project_key": "P"}  # pre-feature row
        restored = _dict_to_profile(d)
        assert restored.ai_adoption == AiAdoptionSignal()


# ── Enriched local-git body capture ────────────────────────────────────────


class TestLocalGitBody:
    def test_captures_co_author_body(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        (repo / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "feat: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>")

        items = local_git_recent_commits(str(repo), days=1)
        assert len(items) == 1
        assert items[0]["title"] == "feat: thing"
        assert "Co-Authored-By: Claude" in items[0]["body"]
        # And the classifier sees it end-to-end.
        assert _classify_ai_markers(items[0]["body"]) == {"claude"}
        # key is now a real short SHA (8 hex chars), not the old constant "local".
        assert items[0]["key"] != "local"
        assert len(items[0]["key"]) == 8 and all(c in "0123456789abcdef" for c in items[0]["key"])
        # No origin remote configured on this test repo → no derived URL.
        assert items[0]["url"] == ""

    def test_no_body_is_empty_string(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        (repo / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "no body here")

        items = local_git_recent_commits(str(repo), days=1)
        assert items[0]["body"] == ""

    def test_derives_github_url_from_origin(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())

        git("init", "-q")
        git("config", "user.email", "dev@example.com")
        git("config", "user.name", "Dev Person")
        git("remote", "add", "origin", "git@github.com:owner/repo.git")
        (repo / "a.txt").write_text("hi")
        git("add", ".")
        git("commit", "-q", "-m", "feat: thing")

        items = local_git_recent_commits(str(repo), days=1)
        assert items[0]["url"].startswith("https://github.com/owner/repo/commit/")
        # The URL carries the full SHA; the key is the short form.
        assert items[0]["url"].split("/commit/")[1].startswith(items[0]["key"])


# ── TUI card builder ───────────────────────────────────────────────────────


def _render_lines(ctx) -> str:
    import io

    from rich.console import Console, Group

    output = io.StringIO()
    console = Console(file=output, width=100, color_system=None)
    console.print(Group(*ctx.lines))
    return output.getvalue()


class TestAiAdoptionCard:
    def _ctx(self, examples):
        from yeaboi.ui.mode_select.screens._analysis_sections import _TaCtx

        return _TaCtx(width=100, examples=examples)

    def test_renders_footprint_and_disclaimer(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _ta_ai_adoption

        sig = AiAdoptionSignal(
            scanned_commits=10,
            scanned_prs=2,
            ai_commits=4,
            ai_prs=1,
            footprint_pct=42.0,
            per_tool=(("claude", 5),),
            per_author=(("Alice", 5),),
            per_activity=(("code", 4), ("pr", 1)),
            sources_scanned=("github",),
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        examples = {
            "ai_adoption": {
                "insights": {
                    "start": [{"title": "Do X", "detail": "d", "evidence": "e"}],
                    "stop": [],
                    "keep": [],
                    "try": [],
                }
            }
        }
        ctx = self._ctx(examples)
        _ta_ai_adoption(ctx, profile)
        out = _render_lines(ctx)
        assert "LOWER BOUND SIGNAL" in out
        assert "42%" in out
        assert "claude" in out
        assert "Do X" in out

    def test_renders_source_provenance_examples_and_links(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _ta_ai_adoption

        sig = AiAdoptionSignal(
            scanned_commits=134,
            ai_commits=133,
            footprint_pct=99.0,
            per_tool=(("claude", 131), ("other_ai", 2)),
            per_author=(("Dinho", 53),),
            per_activity=(("code", 127), ("docs", 6)),
            per_source=(("azdo", 133),),
            repos_scanned=("Azure DevOps (remote): TeamProject",),
            sources_scanned=("azdo",),
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        examples = {
            "ai_adoption": {
                "coverage": ["github: STANDUP_GITHUB_REPO / GITHUB_TOKEN not set"],
                "samples": [
                    {
                        "tool": "claude",
                        "activity": "code",
                        "title": "Fix login",
                        "source": "azdo",
                        "key": "a1b2c3d4",
                        "url": "https://github.com/o/r/commit/a1b2c3d4",
                    }
                ],
                "insights": {
                    "start": [
                        {
                            "title": "Open PRs",
                            "detail": "d",
                            "evidence": "e",
                            "link": "https://github.com/o/r/commit/a1b2c3d4",
                        }
                    ],
                    "stop": [],
                    "keep": [],
                    "try": [],
                },
            }
        }
        ctx = self._ctx(examples)
        _ta_ai_adoption(ctx, profile)
        out = _render_lines(ctx)
        assert "Azure DevOps (remote)" in out  # friendly source label (not raw "azdo")
        assert "TeamProject" in out  # Scanned line names the remote project
        assert "Sources" in out
        assert "Not scanned" in out and "STANDUP_GITHUB_REPO" in out  # coverage shown even when populated
        assert "Evidence" in out and "Fix login" in out  # real example rendered
        assert "https://github.com/o/r/commit/a1b2c3d4" in out  # link on the coaching item

    def test_empty_state_when_not_scanned(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _ta_ai_adoption

        profile = TeamProfile(team_id="t", source="jira", project_key="P")  # default empty signal
        ctx = self._ctx({"ai_adoption": {"coverage": ["github: STANDUP_GITHUB_REPO / GITHUB_TOKEN not set"]}})
        _ta_ai_adoption(ctx, profile)
        out = _render_lines(ctx)
        assert "No AI-usage scan" in out
        assert "STANDUP_GITHUB_REPO" in out
