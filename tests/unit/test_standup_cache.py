import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from yeaboi.standup.cache import StandupMetadataCache
from yeaboi.standup.collector import (
    SOURCE_CONFLUENCE,
    SOURCE_JIRA,
    SOURCE_NOTION,
    collect_recent_activity,
)
from yeaboi.tools.github import _github_changed_files


def test_independent_sources_start_concurrently(monkeypatch):
    barrier = threading.Barrier(3)

    def _fetch(label):
        barrier.wait(timeout=1)
        return [{"author": "A", "kind": "item", "title": label, "key": label}]

    monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda *args, **kwargs: _fetch("jira"))
    monkeypatch.setattr("yeaboi.tools.confluence.confluence_recent_pages", lambda *args, **kwargs: _fetch("docs"))
    monkeypatch.setattr("yeaboi.tools.notion.notion_recent_pages", lambda *args, **kwargs: _fetch("notion"))

    bundle = collect_recent_activity(
        sources={SOURCE_JIRA, SOURCE_CONFLUENCE, SOURCE_NOTION},
        jira_project="P",
        confluence_space="ENG",
        notion_root="workspace",
    )

    assert dict(bundle.counts) == {SOURCE_JIRA: 1, SOURCE_CONFLUENCE: 1, SOURCE_NOTION: 1}


def test_collector_reports_source_progress(monkeypatch):
    monkeypatch.setattr("yeaboi.tools.notion.notion_recent_pages", lambda *args, **kwargs: [])
    progress = []

    collect_recent_activity(
        sources={SOURCE_NOTION},
        notion_root="workspace",
        on_progress=progress.append,
    )

    assert progress[0] == "Running concurrently · Notion"
    assert progress[-1] == "Sources 1/1 · Notion complete (0)"


def test_collector_names_the_source_that_is_still_running(monkeypatch):
    release = threading.Event()
    waiting_status = threading.Event()
    progress = []

    monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", lambda *args, **kwargs: [])

    def slow_notion(*_args, **_kwargs):
        release.wait(timeout=1)
        return []

    monkeypatch.setattr("yeaboi.tools.notion.notion_recent_pages", slow_notion)

    def record(message):
        progress.append(message)
        if message == "Still running · Notion":
            waiting_status.set()

    worker = threading.Thread(
        target=lambda: collect_recent_activity(
            sources={SOURCE_JIRA, SOURCE_NOTION},
            jira_project="P",
            notion_root="workspace",
            on_progress=record,
        )
    )
    worker.start()
    assert waiting_status.wait(timeout=1)
    release.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert "Still running · Notion" in progress


def test_metadata_cache_singleflight_and_persistence(tmp_path):
    cache = StandupMetadataCache(tmp_path / "sessions.db")
    calls = 0
    lock = threading.Lock()

    def _compute():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        return ["docs/guide.md"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: cache.get_or_compute("github", "changed_files", "acme/api:abc", "abc", _compute),
                range(4),
            )
        )
    cache.close()

    reopened = StandupMetadataCache(tmp_path / "sessions.db")
    persisted = reopened.get("github", "changed_files", "acme/api:abc", "abc")
    reopened.close()

    assert results == [["docs/guide.md"]] * 4
    assert calls == 1
    assert persisted == ["docs/guide.md"]


def test_github_changed_files_reuses_revision_cache(tmp_path):
    cache = StandupMetadataCache(tmp_path / "sessions.db")
    calls = 0

    class _Value:
        def get_files(self):
            nonlocal calls
            calls += 1
            return [SimpleNamespace(filename="README.md")]

    value = _Value()
    first = _github_changed_files(value, metadata_cache=cache, object_key="acme/api:#1", revision="r1")
    second = _github_changed_files(value, metadata_cache=cache, object_key="acme/api:#1", revision="r1")
    refreshed = _github_changed_files(value, metadata_cache=cache, object_key="acme/api:#1", revision="r2")
    cache.close()

    assert first == second == refreshed == ["README.md"]
    assert calls == 2
