"""The code-family fetchers: two requests deep, and still bounded.

GitLab and Bitbucket both walk projects first and pipelines second, so the
capture here routes by URL rather than replaying one body. The guard being
asserted is the same as `test_connectors_fetch.py`'s: what was asked for, what
was refused, and that no free text from the vendor reaches an event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from yeaboi.connectors import bitbucket, gitlab
from yeaboi.ops.events import EVENT_KINDS, OpsEvent

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 15, tzinfo=timezone.utc)

# Planted in every free-text field the vendors really return (descriptions,
# commit titles). None of it may reach an OpsEvent.
LEAK_CANARY = "PASSWORD=hunter2 at /srv/app/handlers.py line 88"


class Router:
    """A stand-in for ``httpx.get`` that answers by URL substring, in order."""

    def __init__(self, routes: list[tuple[str, object]]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}))
        for fragment, payload in self.routes:
            if fragment in url:
                return SimpleNamespace(status_code=200, json=lambda p=payload: p, content=b"{}")
        return SimpleNamespace(status_code=404, json=lambda: {}, content=b"{}")

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


def install(monkeypatch, routes: list[tuple[str, object]]) -> Router:
    router = Router(routes)
    monkeypatch.setattr("httpx.get", router)
    monkeypatch.setattr("yeaboi.connectors.http.assert_safe_url", lambda url: url)
    return router


GITLAB_PROJECTS = [
    {"id": 7, "path_with_namespace": "acme/web", "description": LEAK_CANARY},
]

GITLAB_PIPELINES = [
    {
        "id": 900,
        "iid": 12,
        "status": "failed",
        "ref": "main",
        "web_url": "https://gitlab.com/acme/web/-/pipelines/900",
        "created_at": "2026-06-10T09:00:00Z",
        "updated_at": "2026-06-10T09:20:00Z",
    },
    {
        "id": 901,
        "iid": 13,
        "status": "success",
        "ref": "main",
        "web_url": "https://gitlab.com/acme/web/-/pipelines/901",
        "created_at": "2026-06-11T09:00:00Z",
        "updated_at": "2026-06-11T09:15:00Z",
    },
    {"id": 902, "iid": 14, "status": "running", "ref": "main", "created_at": "2026-06-12T09:00:00Z"},
]

BITBUCKET_REPOS = {
    "values": [
        {"slug": "web", "description": LEAK_CANARY},
    ]
}

BITBUCKET_PIPELINES = {
    "values": [
        {
            "uuid": "{u1}",
            "build_number": 41,
            "state": {"name": "COMPLETED", "result": {"name": "FAILED"}},
            "target": {"ref_name": "main", "commit": {"message": LEAK_CANARY}},
            "created_on": "2026-06-10T09:00:00Z",
            "completed_on": "2026-06-10T09:20:00Z",
        },
        {
            "uuid": "{u2}",
            "build_number": 40,
            "state": {"name": "IN_PROGRESS"},
            "target": {"ref_name": "main"},
            "created_on": "2026-06-09T09:00:00Z",
        },
        {
            "uuid": "{u0}",
            "build_number": 2,
            "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}},
            "target": {"ref_name": "main"},
            "created_on": "2020-01-01T00:00:00Z",
        },
    ]
}


@pytest.fixture
def connected(monkeypatch):
    for env, value in {
        "GITLAB_TOKEN": "gl-tok",
        "GITLAB_BASE_URL": "",
        "BITBUCKET_EMAIL": "dev@acme.test",
        "BITBUCKET_API_TOKEN": "bb-tok",
        "BITBUCKET_WORKSPACE": "acme",
    }.items():
        monkeypatch.setenv(env, value)


class TestGitLab:
    def routes(self):
        return [("/api/v4/projects?", GITLAB_PROJECTS), ("/pipelines?", GITLAB_PIPELINES)]

    def test_narrows_projects_to_the_window_before_reading_pipelines(self, monkeypatch, connected):
        router = install(monkeypatch, self.routes())
        gitlab.fetch(START, END)
        first, second = router.urls()
        assert first.startswith("https://gitlab.com/api/v4/projects?")
        assert "membership=true" in first and "last_activity_after=2026-06-01T00%3A00%3A00Z" in first
        assert second.startswith("https://gitlab.com/api/v4/projects/7/pipelines?")
        assert "updated_after=" in second
        assert all(headers == {"PRIVATE-TOKEN": "gl-tok"} for _, headers in router.calls)

    def test_keeps_finished_runs_and_grades_a_failure_high(self, monkeypatch, connected):
        install(monkeypatch, self.routes())
        found = gitlab.fetch(START, END)
        assert [e.status for e in found] == ["failed", "success"]  # running dropped
        assert (found[0].kind, found[0].severity, found[1].severity) == ("deploy", "high", "info")
        assert found[0].ref == "acme/web#12"
        assert found[0].service == "acme/web"

    def test_a_self_hosted_base_url_is_honoured(self, monkeypatch, connected):
        monkeypatch.setenv("GITLAB_BASE_URL", "https://git.acme.test/")
        router = install(monkeypatch, self.routes())
        gitlab.fetch(START, END)
        assert router.urls()[0].startswith("https://git.acme.test/api/v4/projects?")

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, [("/api/v4/projects?", {"unexpected": "shape"})])
        assert gitlab.fetch(START, END) == ()


class TestBitbucket:
    def routes(self):
        return [("/repositories/acme?", BITBUCKET_REPOS), ("/pipelines/", BITBUCKET_PIPELINES)]

    def test_walks_the_workspace_with_basic_auth(self, monkeypatch, connected):
        router = install(monkeypatch, self.routes())
        bitbucket.fetch(START, END)
        first, second = router.urls()
        assert first.startswith("https://api.bitbucket.org/2.0/repositories/acme?")
        assert second.startswith("https://api.bitbucket.org/2.0/repositories/acme/web/pipelines/")
        assert all(h["Authorization"].startswith("Basic ") for _, h in router.calls)

    def test_keeps_completed_runs_and_stops_at_the_window_edge(self, monkeypatch, connected):
        install(monkeypatch, self.routes())
        found = bitbucket.fetch(START, END)
        # IN_PROGRESS is dropped; the 2020 run is behind the window edge and,
        # because rows come newest first, ends the walk rather than being skipped.
        assert [e.ref for e in found] == ["web#41"]
        assert (found[0].kind, found[0].severity, found[0].status) == ("deploy", "high", "failed")
        assert found[0].url == "https://bitbucket.org/acme/web/pipelines/results/41"

    def test_the_workspace_is_quoted_into_the_path(self, monkeypatch, connected):
        monkeypatch.setenv("BITBUCKET_WORKSPACE", "acme/../evil")
        router = install(monkeypatch, [("/repositories/", {"values": []})])
        bitbucket.fetch(START, END)
        assert "/repositories/acme%2F..%2Fevil?" in router.urls()[0]

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, [("/repositories/acme?", {"values": "not a list"})])
        assert bitbucket.fetch(START, END) == ()


class TestNoBodyCrossesTheBoundary:
    CASES = [
        (gitlab, [("/api/v4/projects?", GITLAB_PROJECTS), ("/pipelines?", GITLAB_PIPELINES)]),
        (bitbucket, [("/repositories/acme?", BITBUCKET_REPOS), ("/pipelines/", BITBUCKET_PIPELINES)]),
    ]

    @pytest.mark.parametrize(("module", "routes"), CASES, ids=lambda v: getattr(v, "__name__", ""))
    def test_the_canary_never_comes_back(self, monkeypatch, connected, module, routes):
        install(monkeypatch, routes)
        found = module.fetch(START, END)
        assert found, f"{module.__name__} returned nothing — the guard would pass vacuously"
        for event in found:
            assert LEAK_CANARY not in repr(event)

    @pytest.mark.parametrize(("module", "routes"), CASES, ids=lambda v: getattr(v, "__name__", ""))
    def test_every_fetcher_returns_ops_events_of_a_known_kind(self, monkeypatch, connected, module, routes):
        install(monkeypatch, routes)
        for event in module.fetch(START, END):
            assert isinstance(event, OpsEvent)
            assert event.kind in EVENT_KINDS
            assert event.source == module.CONNECTOR.key
