"""Unit tests for reporting/context.py — supporting code/docs signals."""

import pytest

from yeaboi.agent.state import SupportingSignal
from yeaboi.reporting import context
from yeaboi.standup.collector import ActivityBundle


@pytest.fixture(autouse=True)
def _no_config(monkeypatch):
    # Keep config probes inert so tests never see the developer's real env.
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "org/repo", raising=False)
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Team", raising=False)


def _bundle(items=(), errors=()):
    return ActivityBundle(items=list(items), errors=list(errors))


def _patch_collector(monkeypatch, bundle, captured=None):
    def _fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return bundle

    monkeypatch.setattr("yeaboi.standup.collector.collect_recent_activity", _fake)


def _patch_docs(monkeypatch, pages=(), notes=(), captured=None):
    def _fake(source, project_key, sub_sources=None, **kwargs):
        if captured is not None:
            captured["sub_sources"] = sub_sources
            captured.update(kwargs)
        return list(pages), ["confluence"], list(notes)

    monkeypatch.setattr("yeaboi.analysis.doc_quality.collect_doc_pages", _fake)


class TestGatherSupportingSignals:
    def test_empty_selections_do_no_fetching(self, monkeypatch):
        def _boom(**kwargs):
            raise AssertionError("no fetch expected")

        monkeypatch.setattr("yeaboi.standup.collector.collect_recent_activity", _boom)
        monkeypatch.setattr("yeaboi.analysis.doc_quality.collect_doc_pages", _boom)
        signals, warnings = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=[], doc_sources=[]
        )
        assert signals == () and warnings == []

    def test_code_signals_group_count_and_canonicalize(self, monkeypatch):
        items = [
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": "Fix auth",
                "key": "#41",
                "timestamp": "2026-07-05",
            },
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": "Add SSO",
                "key": "#44",
                "timestamp": "2026-07-06",
            },
            {"source": "azdo_repos", "kind": "commit", "title": "tidy", "key": "abc123", "timestamp": "2026-07-07"},
            {"source": "github", "kind": "review", "title": "LGTM", "key": "#41", "timestamp": "2026-07-07"},
        ]
        captured: dict = {}
        _patch_collector(monkeypatch, _bundle(items), captured)
        signals, warnings = context.gather_supporting_signals(
            period_start="2026-07-01",
            period_end="2026-07-14",
            code_sources=["github", "azuredevops"],
            doc_sources=[],
        )
        by_key = {(s.kind, s.source): s for s in signals}
        assert by_key[("pull_requests", "github")].count == 2
        assert by_key[("pull_requests", "github")].samples == ("Fix auth (#41)", "Add SSO (#44)")
        assert by_key[("commits", "azuredevops")].count == 1  # azdo_repos → canonical token
        assert ("reviews", "github") not in by_key  # reviews ignored
        assert not warnings
        # PRs sort ahead of commits — they corroborate delivery best.
        assert signals[0].kind == "pull_requests"

    def test_unmerged_prs_are_not_counted(self, monkeypatch):
        # Every surface phrases the count as "merged PRs" — open/active/closed
        # (unmerged) PRs from the all-states fetch must not inflate it.
        items = [
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": "shipped",
                "key": "#1",
                "timestamp": "2026-07-05",
            },
            {
                "source": "github",
                "kind": "pr",
                "status": "open",
                "title": "still open",
                "key": "#2",
                "timestamp": "2026-07-06",
            },
            {
                "source": "azdo_repos",
                "kind": "pr",
                "status": "active",
                "title": "in review",
                "key": "9",
                "timestamp": "2026-07-06",
            },
            {"source": "github", "kind": "commit", "title": "tidy", "key": "abc", "timestamp": "2026-07-06"},
        ]
        _patch_collector(monkeypatch, _bundle(items))
        signals, _ = context.gather_supporting_signals(
            period_start="2026-07-01",
            period_end="2026-07-14",
            code_sources=["github", "azuredevops"],
            doc_sources=[],
        )
        by_key = {(s.kind, s.source): s.count for s in signals}
        assert by_key[("pull_requests", "github")] == 1
        assert ("pull_requests", "azuredevops") not in by_key
        assert by_key[("commits", "github")] == 1  # commits keep no status filter

    def test_since_derived_from_period_start_and_sources_mapped(self, monkeypatch):
        captured: dict = {}
        _patch_collector(monkeypatch, _bundle(), captured)
        context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=["github"], doc_sources=[]
        )
        assert captured["since"] is not None and captured["since"].date().isoformat() == "2026-07-01"
        assert captured["sources"] == {"github"}
        assert captured["github_repo"] == "org/repo"

    def test_items_outside_period_clamped_undated_kept(self, monkeypatch):
        items = [
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": "in window",
                "key": "#1",
                "timestamp": "2026-07-10",
            },
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": "too new",
                "key": "#2",
                "timestamp": "2026-07-20",
            },
            {"source": "github", "kind": "pr", "status": "merged", "title": "undated", "key": "#3", "timestamp": ""},
        ]
        _patch_collector(monkeypatch, _bundle(items))
        signals, _ = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=["github"], doc_sources=[]
        )
        assert signals[0].count == 2  # "too new" dropped, undated kept

    def test_sample_bounding_and_title_truncation(self, monkeypatch):
        long_title = "x" * 200
        items = [
            {
                "source": "github",
                "kind": "pr",
                "status": "merged",
                "title": f"{long_title}{i}",
                "key": f"#{i}",
                "timestamp": "2026-07-05",
            }
            for i in range(9)
        ]
        _patch_collector(monkeypatch, _bundle(items))
        signals, _ = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=["github"], doc_sources=[]
        )
        sig = signals[0]
        assert sig.count == 9
        assert len(sig.samples) == 5  # bounded
        title_part = sig.samples[0].rsplit(" (", 1)[0]
        assert len(title_part) == 120 and title_part.endswith("…")

    def test_bundle_errors_become_warnings(self, monkeypatch):
        _patch_collector(monkeypatch, _bundle(errors=[("github", "401 bad credentials")]))
        signals, warnings = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=["github"], doc_sources=[]
        )
        assert signals == ()
        assert warnings == ["Code context from github unavailable — 401 bad credentials"]

    def test_doc_signals_group_by_platform_and_pass_sub_sources(self, monkeypatch):
        pages = [
            {"platform": "confluence", "title": "Runbook", "timestamp": "2026-07-04"},
            {"platform": "confluence", "title": "ADR-7", "timestamp": "2026-07-05"},
            {"platform": "notion", "title": "Spec", "timestamp": "2026-07-06"},
            {"platform": "notion", "title": "future page", "timestamp": "2026-08-01"},
        ]
        captured: dict = {}
        _patch_docs(monkeypatch, pages, notes=["Notion root X not readable"], captured=captured)
        signals, warnings = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=[], doc_sources=["confluence", "notion"]
        )
        assert captured["sub_sources"] == ["confluence", "notion"]
        assert captured["window_days"] >= 1
        by_source = {s.source: s for s in signals}
        assert by_source["confluence"].count == 2
        assert by_source["confluence"].kind == "doc_updates"
        assert by_source["notion"].count == 1  # future page clamped
        assert warnings == ["Docs context: Notion root X not readable"]

    def test_fetcher_exception_becomes_warning_not_crash(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("collector exploded")

        monkeypatch.setattr("yeaboi.standup.collector.collect_recent_activity", _boom)
        signals, warnings = context.gather_supporting_signals(
            period_start="2026-07-01", period_end="2026-07-14", code_sources=["github"], doc_sources=[]
        )
        assert signals == ()
        assert warnings and "collector exploded" in warnings[0]

    def test_progress_emitted(self, monkeypatch):
        _patch_collector(
            monkeypatch, _bundle([{"source": "github", "kind": "pr", "status": "merged", "title": "t", "key": "#1"}])
        )
        seen: list[str] = []
        context.gather_supporting_signals(
            period_start="2026-07-01",
            period_end="2026-07-14",
            code_sources=["github"],
            doc_sources=[],
            on_progress=seen.append,
        )
        assert any("code activity" in m.lower() for m in seen)
        assert any("Supporting signals:" in m for m in seen)


def _doc_event(detail, *, status="running", component="docs:documentation"):
    return {
        "kind": "analysis_component",
        "component_id": component,
        "label": "Assessing documentation quality",
        "status": status,
        "detail": detail,
    }


class TestProgressProxy:
    """Structured doc-collector events must reach the UI as prose, not dict reprs."""

    def _proxy(self):
        seen: list[str] = []
        return context._ProgressProxy(seen.append), seen

    def test_plain_strings_pass_through(self):
        proxy, seen = self._proxy()
        proxy.append("Reading recent doc updates…")
        assert seen == ["Reading recent doc updates…"]

    def test_event_flattens_to_detail_text_never_dict_repr(self):
        proxy, seen = self._proxy()
        proxy.append(_doc_event("Discovered 22 documentation pages"))
        assert seen == ["Discovered 22 documentation pages"]

    def test_event_without_detail_falls_back_to_label(self):
        proxy, seen = self._proxy()
        proxy.append(_doc_event(""))
        assert seen == ["Assessing documentation quality"]

    def test_counter_ticks_collapse_to_one_line(self):
        proxy, seen = self._proxy()
        for i in range(1, 23):
            proxy.append(_doc_event(f"Reading documentation: {i}/22 · 0 failed"))
        assert seen == ["Reading documentation: 1/22 · 0 failed"]

    def test_distinct_milestones_still_emit(self):
        proxy, seen = self._proxy()
        proxy.append(_doc_event("Discovering Confluence space PSO: 0 found · batch 0"))
        proxy.append(_doc_event("Discovered 22 documentation pages"))
        proxy.append(_doc_event("Reading documentation: 1/22 · 0 failed"))
        proxy.append(_doc_event("Reading documentation: 2/22 · 0 failed"))
        assert seen == [
            "Discovering Confluence space PSO: 0 found · batch 0",
            "Discovered 22 documentation pages",
            "Reading documentation: 1/22 · 0 failed",
        ]

    def test_status_change_breaks_the_collapse(self):
        proxy, seen = self._proxy()
        proxy.append(_doc_event("Reading documentation: 21/22 · 0 failed"))
        proxy.append(_doc_event("Reading documentation: 22/22 · 0 failed", status="completed"))
        assert len(seen) == 2

    def test_all_messages_still_recorded_on_the_list_itself(self):
        # collect_doc_pages reads the list back for coverage notes — appends must land.
        proxy, seen = self._proxy()
        proxy.append(_doc_event("Reading documentation: 1/9 · 0 failed"))
        proxy.append(_doc_event("Reading documentation: 2/9 · 0 failed"))
        assert len(proxy) == 2 and len(seen) == 1

    def test_broken_callback_never_raises(self):
        def _boom(_msg):
            raise RuntimeError("ui went away")

        proxy = context._ProgressProxy(_boom)
        proxy.append(_doc_event("Discovered 3 documentation pages"))  # must not raise


class TestSignalsSentence:
    def test_empty(self):
        assert context.signals_sentence(()) == ""
        assert context.signals_sentence((SupportingSignal(kind="pull_requests", count=0),)) == ""

    def test_composition_and_pluralization(self):
        signals = (
            SupportingSignal(kind="pull_requests", source="github", count=24),
            SupportingSignal(kind="commits", source="azuredevops", count=1),
            SupportingSignal(kind="doc_updates", source="notion", count=5),
        )
        assert context.signals_sentence(signals) == "Corroborated by 24 merged PRs, 1 commit and 5 doc updates"

    def test_single_kind(self):
        assert (
            context.signals_sentence((SupportingSignal(kind="doc_updates", count=2),))
            == "Corroborated by 2 doc updates"
        )
