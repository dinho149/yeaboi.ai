"""Tests for the documentation-quality sub-analysis (analysis/doc_quality.py) and its wiring.

Covers: deterministic clarity/usefulness metrics, pure aggregation, explicit AI-marker
counts, deterministic coaching, graceful page-collection fan-out, and orchestration.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from yeaboi.analysis.doc_quality import (
    _clarity_metrics,
    _fallback_doc_quality_insights,
    _read_page_inventory,
    aggregate_doc_quality,
    collect_doc_pages,
    generate_doc_quality_insights,
    run_doc_quality,
)
from yeaboi.team_profile import DocQualitySignal

# A plain, human, clear paragraph — short sentences, contractions, no AI tells.
_CLEAR_TEXT = (
    "# Onboarding\n\n"
    "Welcome to the team. Here's how to set up.\n\n"
    "- Clone the repo.\n"
    "- Run the setup script.\n"
    "- Ask if you're stuck.\n\n"
    "That's it. You're ready to go."
)

# A dense, jargon-heavy wall of one very long sentence.
_DENSE_TEXT = (
    "Notwithstanding the aforementioned architectural considerations, the comprehensive "
    "instrumentation subsystem necessitates meticulous reconfiguration across heterogeneous "
    "deployment environments whilst simultaneously accommodating the multifarious "
    "interdependencies inherent to distributed computational infrastructures and their "
    "concomitant orchestration frameworks, thereby precipitating substantial reconsideration."
)

# Verbose prose used to exercise mixed documentation-quality results.
_VERBOSE_TEXT = (
    "Moreover, it is worth noting that this approach will seamlessly leverage a robust, "
    "holistic framework — furthermore, it is important to note the paramount role of "
    "streamlined processes. Additionally, this serves to facilitate a testament to "
    "excellence. In conclusion, we delve into the realm of possibility — notably underscoring "
    "the crucial nature of the endeavour. Furthermore, this holistic tapestry is paramount."
)


class TestClarityMetrics:
    def test_clear_scores_higher_than_dense(self):
        clear = _clarity_metrics(_CLEAR_TEXT)["clarity"]
        dense = _clarity_metrics(_DENSE_TEXT)["clarity"]
        assert clear > dense
        assert clear >= 60  # plain-English band

    def test_empty_text_is_zero(self):
        m = _clarity_metrics("")
        assert m["clarity"] == 0.0
        assert m["word_count"] == 0

    def test_reports_structure(self):
        m = _clarity_metrics(_CLEAR_TEXT)
        assert m["heading_count"] >= 1
        assert m["has_lists"] is True

    def test_long_sentence_pct(self):
        m = _clarity_metrics(_DENSE_TEXT)
        assert m["long_sentence_pct"] > 0


class TestCodeAwareScoring:
    def test_fenced_code_excluded_from_readability(self):
        from yeaboi.analysis.doc_quality import _usefulness_metrics

        prose = "Run the deploy. It is safe. Check the logs. All good."
        code = "```\nkubernetes_deployment_reconciliation_orchestrator --enable-multiregional-failover\n```\n"
        plain = _clarity_metrics(prose)
        with_code = _clarity_metrics(code + prose)
        assert plain["has_code_blocks"] is False
        assert with_code["has_code_blocks"] is True
        # The fenced identifiers must not drag the Flesch score down.
        assert with_code["clarity"] == plain["clarity"]
        assert _usefulness_metrics(code + prose)  # signal only — no crash, no weight change

    def test_owner_table_row_detected(self):
        from yeaboi.analysis.doc_quality import _usefulness_metrics

        assert _usefulness_metrics("Owner | Jane\n\nRun the procedure below.")["owned"] is True

    def test_bold_owner_line_detected(self):
        from yeaboi.analysis.doc_quality import _usefulness_metrics

        assert _usefulness_metrics("**Owner**: Jane")["owned"] is True


class TestScoringCacheVersion:
    def test_v2_cached_scores_not_reused_after_bump(self, tmp_path):
        from yeaboi.analysis.doc_quality import _DOC_CACHE_TASK, _DOC_SCORING_VERSION
        from yeaboi.team_profile import TeamProfileStore

        assert _DOC_SCORING_VERSION != "deterministic-v2"
        with TeamProfileStore(tmp_path / "db.sqlite") as store:
            store.save_analysis_enrichment(_DOC_CACHE_TASK, "page-key", "deterministic-v2", {"clarity": 10.0})
            assert store.load_analysis_enrichment(_DOC_CACHE_TASK, "page-key", _DOC_SCORING_VERSION) is None


class TestAggregate:
    def _pages(self):
        return [
            {"platform": "confluence", "title": "Clear one", "text": _CLEAR_TEXT},
            {"platform": "confluence", "title": "Dense one", "text": _DENSE_TEXT},
            {"platform": "notion", "title": "Verbose one", "text": _VERBOSE_TEXT},
        ]

    def test_counts_and_distribution(self):
        sig = aggregate_doc_quality(self._pages())
        assert sig.pages_scanned == 3
        assert set(sig.platforms_scanned) == {"confluence", "notion"}
        assert sig.clear_pages + sig.mixed_pages + sig.unclear_pages == 3
        assert dict(sig.per_platform) == {"confluence": 2, "notion": 1}
        assert sig.is_ai_estimate is False

    def test_usefulness_replaces_ai_estimate(self):
        sig = aggregate_doc_quality(self._pages())
        assert sig.avg_usefulness > 0
        assert sig.likely_ai_pages == 0
        assert sig.avg_ai_likelihood == 0

    def test_explicit_marker_counted_as_lower_bound(self):
        disclosed = "Draft notes. Co-Authored-By: Claude <noreply@anthropic.com>"
        pages = [
            {"platform": "notion", "title": "Disclosed", "text": disclosed},
            {"platform": "notion", "title": "Plain", "text": _CLEAR_TEXT},
        ]
        sig = aggregate_doc_quality(pages)
        assert sig.ai_marked_pages == 1

    def test_page_about_ai_tools_is_not_marked(self):
        # Regression: a page that documents AI tooling (pasting a marker address
        # or URL as an example) is ABOUT AI, not written by it.
        about = "AI tooling guide. Example trailer address: copilot@github.com. See https://claude.com/claude-code."
        sig = aggregate_doc_quality([{"platform": "notion", "title": "About AI", "text": about}])
        assert sig.ai_marked_pages == 0

    def test_flagged_pages_populated(self):
        sig = aggregate_doc_quality(self._pages())
        # Low-quality pages surface as call-outs.
        assert sig.flagged_pages
        titles = {t for t, _ in sig.flagged_pages}
        assert "Dense one" in titles or "Verbose one" in titles

    def test_empty_returns_zeros(self):
        sig = aggregate_doc_quality([])
        assert sig == DocQualitySignal()
        assert sig.pages_scanned == 0


class TestFallbackInsights:
    def test_all_categories_non_empty_low_clarity(self):
        sig = DocQualitySignal(pages_scanned=5, avg_clarity=42.0, unclear_pages=2, avg_usefulness=35.0)
        out = _fallback_doc_quality_insights(sig)
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_all_categories_non_empty_empty_signal(self):
        out = _fallback_doc_quality_insights(DocQualitySignal())
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_ownerless_docs_trigger_stop(self):
        sig = DocQualitySignal(pages_scanned=4, avg_clarity=70.0, owned_pages=1)
        out = _fallback_doc_quality_insights(sig)
        blob = " ".join(it["detail"] + it["evidence"] for it in out["stop"]).lower()
        assert "owner" in blob

    def test_cites_least_clear_page_with_link(self):
        sig = DocQualitySignal(pages_scanned=3, avg_clarity=45.0, unclear_pages=1)
        samples = [
            {"title": "Clear one", "platform": "notion", "clarity": 80, "usefulness": 80, "url": "u1"},
            {"title": "Dense one", "platform": "confluence", "clarity": 25, "usefulness": 20, "url": "u2"},
        ]
        out = _fallback_doc_quality_insights(sig, samples)
        tighten = next(it for it in out["start"] if "Rewrite" in it["title"])
        assert "Dense one" in tighten["evidence"]  # the least-clear page
        assert tighten["link"] == "u2"


class TestGenerateInsights:
    _SIG = DocQualitySignal(
        pages_scanned=6,
        platforms_scanned=("confluence", "notion"),
        avg_clarity=54.0,
        clear_pages=2,
        mixed_pages=2,
        unclear_pages=2,
        ai_marked_pages=1,
        avg_usefulness=48.0,
        owned_pages=2,
        actionable_pages=3,
        per_platform=(("confluence", 4), ("notion", 2)),
        flagged_pages=(("Onboarding", "clarity 30/100 — dense or long-winded"),),
    )

    def test_uses_structured_page_results_without_llm(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._llm_invoke",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
        )
        out = generate_doc_quality_insights(
            self._SIG,
            {"samples": [{"title": "Dense", "platform": "notion", "clarity": 20, "usefulness": 20}]},
        )
        assert "Rewrite" in out["start"][0]["title"]
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_empty_examples_use_deterministic_skeleton(self, monkeypatch):
        out = generate_doc_quality_insights(self._SIG, {})
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_llm_failure_falls_back(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no llm")

        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", boom)
        out = generate_doc_quality_insights(self._SIG, {})
        assert all(out[c] for c in ("start", "stop", "keep", "try"))

    def test_page_evidence_link_is_preserved(self, monkeypatch):
        good = "https://notion.so/page-123"
        examples = {"samples": [{"url": good, "title": "x", "platform": "notion", "clarity": 40, "usefulness": 20}]}
        out = generate_doc_quality_insights(self._SIG, examples)
        assert out["start"][0]["link"] == good


class TestCollectDocPages:
    def test_no_config_records_coverage_gaps(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)
        pages, platforms, coverage = collect_doc_pages("jira", "PROJ")
        assert pages == []
        assert platforms == []
        assert any("confluence" in c for c in coverage)
        assert any("notion" in c for c in coverage)

    def test_confluence_pages_tagged_and_deduped(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x.atlassian.net/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)
        # Two recent items for the SAME page id (Confluence emits one per editor) → one read.
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_recent_pages",
            lambda days=1: [
                {"key": "123", "title": "Guide", "author": "A", "url": "u", "timestamp": "t"},
                {"key": "123", "title": "Guide", "author": "B", "url": "u", "timestamp": "t"},
            ],
        )
        reads: list[str] = []

        def _read(page_id="", max_chars=0):
            reads.append(page_id)
            return {"title": "Guide", "text": _CLEAR_TEXT, "truncated": False, "error": ""}

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_read_page_text", _read)
        pages, platforms, coverage = collect_doc_pages("jira", "PROJ")
        assert len(pages) == 1  # deduped
        assert reads == ["123"]  # only one body read
        assert pages[0]["platform"] == "confluence"
        assert platforms == ["confluence"]

    def test_announces_discovered_count_before_parallel_body_reads(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x.atlassian.net/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_recent_pages",
            lambda **kwargs: [
                {"key": str(i), "title": f"Page {i}", "url": f"u{i}", "timestamp": "t"} for i in range(3)
            ],
        )
        progress: list = []

        def _read(page_id="", max_chars=0, **kwargs):
            assert any(
                isinstance(item, dict) and item.get("detail") == "Discovered 3 documentation pages" for item in progress
            )
            assert kwargs["request_timeout_seconds"] == 30
            return {"title": f"Page {page_id}", "text": _CLEAR_TEXT, "truncated": False, "error": ""}

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_read_page_text", _read)
        pages, _, _, report = collect_doc_pages(
            "jira",
            "PROJ",
            sub_sources=["confluence"],
            progress=progress,
            _return_coverage=True,
        )
        assert len(pages) == 3
        assert report["discovered"] == report["attempted"] == report["succeeded"] == 3
        assert any(
            isinstance(item, dict) and item.get("detail") == "Reading documentation: 3/3 · 0 failed"
            for item in progress
        )

    def test_configured_spaces_are_discovered_concurrently(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x.atlassian.net/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)
        lock = threading.Lock()
        active = 0
        peak = 0

        def _discover(space_key="", **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return [{"key": space_key, "title": space_key, "version": "1"}]

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_recent_pages", _discover)
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_read_page_text",
            lambda page_id="", **_kwargs: {
                "title": page_id,
                "text": _CLEAR_TEXT,
                "truncated": False,
                "error": "",
            },
        )

        pages, _, _ = collect_doc_pages(
            "jira",
            "PROJ",
            sub_sources=["confluence"],
            analysis_scope={"confluence": ["ONE", "TWO"]},
        )

        assert peak == 2
        assert {page["container"] for page in pages} == {"ONE", "TWO"}

    def test_provider_pools_are_bounded_and_results_keep_discovery_order(self):
        inventory = [
            {
                "platform": provider,
                "container": "scope",
                "key": f"{provider}-{i}",
                "title": f"{provider}-{i}",
            }
            for provider, count in (("confluence", 16), ("notion", 6))
            for i in range(count)
        ]
        lock = threading.Lock()
        active = {"confluence": 0, "notion": 0}
        peak = {"confluence": 0, "notion": 0}

        def _reader(provider):
            def _read(page_id):
                with lock:
                    active[provider] += 1
                    peak[provider] = max(peak[provider], active[provider])
                time.sleep(0.01)
                with lock:
                    active[provider] -= 1
                return {"title": page_id, "text": _CLEAR_TEXT, "truncated": False, "error": ""}

            return _read

        pages = _read_page_inventory(
            inventory,
            {"confluence": _reader("confluence"), "notion": _reader("notion")},
        )
        assert [page["key"] for page in pages] == [page["key"] for page in inventory]
        assert 1 < peak["confluence"] <= 8
        assert 1 < peak["notion"] <= 2

    def test_transient_page_read_retries_once(self):
        calls = 0

        def _read(_page_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"text": "", "truncated": False, "error": "429 rate limit"}
            return {"text": _CLEAR_TEXT, "truncated": False, "error": ""}

        pages = _read_page_inventory(
            [{"platform": "confluence", "container": "SPACE", "key": "p1", "title": "Page"}],
            {"confluence": _read},
        )
        assert calls == 2
        assert pages[0]["read_error"] == ""

    def test_version_cache_reuses_derived_asset_without_storing_body(self, tmp_path):
        database = tmp_path / "analysis.db"
        inventory = [
            {
                "platform": "confluence",
                "container": "PSO",
                "key": "p1",
                "version": "7",
                "title": "Runbook",
            }
        ]
        calls = 0

        def _read(_page_id):
            nonlocal calls
            calls += 1
            return {"text": _CLEAR_TEXT, "truncated": False, "error": ""}

        first = _read_page_inventory(inventory, {"confluence": _read}, db_path=database)
        second = _read_page_inventory(inventory, {"confluence": _read}, db_path=database)

        assert calls == 1
        assert first[0]["asset"] == second[0]["asset"]
        assert second[0]["cache_status"] == "hit"
        assert second[0]["text"] == ""
        assert _CLEAR_TEXT not in database.read_bytes().decode("utf-8", errors="ignore")

    def test_version_change_refetches_only_changed_page(self, tmp_path):
        database = tmp_path / "analysis.db"
        inventory = [
            {
                "platform": "notion",
                "container": "root",
                "key": "p1",
                "version": "2026-01-01",
                "title": "Guide",
            }
        ]
        calls = 0

        def _read(_page_id):
            nonlocal calls
            calls += 1
            return {"text": _CLEAR_TEXT, "truncated": False, "error": ""}

        _read_page_inventory(inventory, {"notion": _read}, db_path=database)
        inventory[0]["version"] = "2026-01-02"
        changed = _read_page_inventory(inventory, {"notion": _read}, db_path=database)

        assert calls == 2
        assert changed[0]["cache_status"] == "miss"

    def test_source_error_recorded_not_raised(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)

        def boom(days=1):
            raise RuntimeError("401 Unauthorized")

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_recent_pages", boom)
        pages, platforms, coverage = collect_doc_pages("jira", "PROJ")
        assert pages == []
        assert any("confluence: error" in c for c in coverage)

    def test_partial_confluence_discovery_reads_recovered_pages(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: None)
        monkeypatch.setattr(
            "yeaboi.analysis.doc_quality._discover_confluence_pages",
            lambda *args, **kwargs: SimpleNamespace(
                items=[
                    {
                        "key": "123",
                        "title": "Recovered",
                        "author": "A",
                        "url": "u",
                        "timestamp": "t",
                    }
                ],
                expected_total=200,
                complete=False,
                error="repeated page IDs",
            ),
        )
        monkeypatch.setattr(
            "yeaboi.tools.confluence.confluence_read_page_text",
            lambda **kwargs: {
                "title": "Recovered",
                "text": _CLEAR_TEXT,
                "truncated": False,
                "error": "",
            },
        )

        pages, platforms, coverage, report = collect_doc_pages(
            "jira",
            "PROJ",
            sub_sources=["confluence"],
            _return_coverage=True,
        )

        assert len(pages) == 1
        assert platforms == ["confluence"]
        assert report["status"] == "partial"
        assert report["completed"] == 1
        assert report["expected"] == 200
        assert any("repeated page IDs" in note for note in coverage)

    def test_sub_sources_restricts_to_notion_only(self, monkeypatch):
        # Confluence is configured but not requested → skipped; only Notion read.
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x/wiki")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "ntok")

        def _boom(*a, **k):
            raise AssertionError("Confluence must not be read when sub_sources=['notion']")

        monkeypatch.setattr("yeaboi.tools.confluence.confluence_recent_pages", _boom)
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_recent_pages",
            lambda days=1: [{"key": "p1", "title": "Doc", "author": "A", "url": "u", "timestamp": "t"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_read_page_text",
            lambda page_id, max_chars=0: {"title": "Doc", "text": _CLEAR_TEXT, "truncated": False, "error": ""},
        )
        pages, platforms, coverage = collect_doc_pages("jira", "PROJ", sub_sources=["notion"])
        assert platforms == ["notion"]
        # Confluence isn't even reported as a gap — it wasn't requested.
        assert not any("confluence" in c for c in coverage)

    def test_empty_text_pages_dropped(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_token", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "tok")
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_recent_pages",
            lambda days=1: [{"key": "p1", "title": "Empty", "author": "A", "url": "u", "timestamp": "t"}],
        )
        monkeypatch.setattr(
            "yeaboi.tools.notion.notion_read_page_text",
            lambda page_id, max_chars=0: {"title": "Empty", "text": "   ", "truncated": False, "error": ""},
        )
        pages, platforms, coverage, report = collect_doc_pages(
            "jira",
            "PROJ",
            sub_sources=["notion"],
            _return_coverage=True,
        )
        assert pages == []  # blank body dropped
        assert not any("empty page body" in c for c in coverage)
        assert report["status"] == "no_data"
        assert report["eligible"] == 0
        assert report["unchanged"] == 1


class TestRunDocQuality:
    def test_aggregates_collected_pages(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.doc_quality.collect_doc_pages",
            lambda source, project, sub_sources=None: (
                [
                    {"platform": "confluence", "title": "Clear", "text": _CLEAR_TEXT},
                    {"platform": "notion", "title": "Verbose", "text": _VERBOSE_TEXT},
                ],
                ["confluence", "notion"],
                [],
            ),
        )
        signal, blob = run_doc_quality("jira", "PROJ")
        assert signal.pages_scanned == 2
        assert blob["summary"]["pages_scanned"] == 2
        # Samples carry titles/scores only — never page bodies.
        assert blob["samples"]
        assert all("text" not in s for s in blob["samples"])
        assert {"discovery_seconds", "read_seconds", "score_seconds", "total_seconds"} <= set(blob["stage_timings"])

    def test_collect_failure_returns_empty_signal(self, monkeypatch):
        def boom(source, project):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("yeaboi.analysis.doc_quality.collect_doc_pages", boom)
        signal, blob = run_doc_quality("jira", "PROJ")
        assert signal == DocQualitySignal()
        assert blob["coverage"] == ["doc-quality scan failed"]
