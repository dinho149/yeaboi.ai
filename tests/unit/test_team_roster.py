from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from yeaboi.team_roster import fetch_roster_result


def test_fresh_cache_avoids_provider_call(monkeypatch, tmp_path):
    calls = {"count": 0}

    def jira(project, days=30):
        calls["count"] += 1
        return [{"name": "Ada", "identity": "jira:1"}]

    monkeypatch.setattr("yeaboi.tools.jira.jira_assignee_roster", jira)
    db = tmp_path / "sessions.db"
    first = fetch_roster_result(jira_project="PROJ", db_path=db)
    second = fetch_roster_result(jira_project="PROJ", db_path=db)

    assert first.status == second.status == "complete"
    assert [member.name for member in second.members] == ["Ada"]
    assert second.sources[0].from_cache is True
    assert calls["count"] == 1


def test_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    calls = {"count": 0}

    def jira(project, days=30):
        calls["count"] += 1
        return [{"name": f"Person {calls['count']}", "identity": str(calls["count"])}]

    monkeypatch.setattr("yeaboi.tools.jira.jira_assignee_roster", jira)
    db = tmp_path / "sessions.db"
    fetch_roster_result(jira_project="PROJ", db_path=db)
    result = fetch_roster_result(jira_project="PROJ", db_path=db, force_refresh=True)

    assert [member.name for member in result.members] == ["Person 2"]
    assert calls["count"] == 2


def test_stale_cache_is_returned_when_refresh_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "yeaboi.tools.jira.jira_assignee_roster",
        lambda project, days=30: [{"name": "Ada", "identity": "jira:1"}],
    )
    db = tmp_path / "sessions.db"
    fetch_roster_result(jira_project="PROJ", db_path=db)
    monkeypatch.setattr(
        "yeaboi.tools.jira.jira_assignee_roster",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    result = fetch_roster_result(
        jira_project="PROJ",
        db_path=db,
        force_refresh=True,
    )

    assert result.status == "partial"
    assert [member.name for member in result.members] == ["Ada"]
    assert result.sources[0].status == "stale"
    assert result.warnings


def test_sources_are_queried_concurrently(monkeypatch, tmp_path):
    # A Barrier(2) only releases once BOTH fetchers have reached it. If the sources
    # were queried sequentially the first fetcher would block until the timeout and
    # trip BrokenBarrierError, so a clean pass proves they ran concurrently — with no
    # wall-clock threshold to flake on a slow/loaded CI runner (the old `< 0.27s`
    # assertion failed there despite correct concurrency).
    barrier = threading.Barrier(2, timeout=5)

    def slow(name):
        def fetch(project, days=30):
            barrier.wait()
            return [{"name": name, "identity": name}]

        return fetch

    monkeypatch.setattr("yeaboi.tools.jira.jira_assignee_roster", slow("Ada"))
    monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_assignee_roster", slow("Bob"))
    result = fetch_roster_result(jira_project="J", azdo_project="A", db_path=tmp_path / "db")

    assert [member.name for member in result.members] == ["Ada", "Bob"]


def test_total_failure_is_distinct_from_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "yeaboi.tools.jira.jira_assignee_roster",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    failed = fetch_roster_result(jira_project="PROJ", db_path=tmp_path / "failed.db")
    assert failed.status == "failed"

    monkeypatch.setattr("yeaboi.tools.jira.jira_assignee_roster", lambda *args, **kwargs: [])
    empty = fetch_roster_result(jira_project="PROJ", db_path=tmp_path / "empty.db")
    assert empty.status == "empty"


def test_jira_roster_uses_minimal_fields_and_paginates(monkeypatch):
    from yeaboi.tools.jira import jira_assignee_roster

    def issue(index):
        assignee = SimpleNamespace(
            displayName=f"Person {index}",
            emailAddress=f"p{index}@example.com",
            accountId=f"id-{index}",
            accountType="atlassian",
        )
        return SimpleNamespace(fields=SimpleNamespace(assignee=assignee))

    client = MagicMock()
    client.search_issues.side_effect = [
        [issue(index) for index in range(100)],
        [issue(100)],
    ]
    monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)

    result = jira_assignee_roster("PROJ", days=30)

    assert len(result) == 101
    assert client.search_issues.call_count == 2
    assert client.search_issues.call_args_list[0].kwargs["fields"] == "assignee"
    assert "comment" not in client.search_issues.call_args_list[0].kwargs["fields"]
    assert "changelog" not in client.search_issues.call_args_list[0].kwargs
    assert 'statusCategory = "In Progress"' in client.search_issues.call_args_list[0].args[0]


def test_azdo_roster_batches_all_ids_and_uses_assigned_to(monkeypatch):
    from yeaboi.tools.azure_devops import azdevops_assignee_roster

    client = MagicMock()
    query_result = SimpleNamespace(work_items=[SimpleNamespace(id=index) for index in range(250)])
    client.query_by_wiql.return_value = query_result

    def get_items(ids, fields):
        return [
            SimpleNamespace(
                fields={
                    "System.Id": item_id,
                    "System.AssignedTo": {
                        "displayName": f"Person {item_id}",
                        "uniqueName": f"p{item_id}@example.com",
                        "descriptor": f"descriptor-{item_id}",
                    },
                    "System.ChangedBy": {
                        "displayName": "Drive-by editor",
                        "descriptor": "editor",
                    },
                }
            )
            for item_id in ids
        ]

    client.get_work_items.side_effect = get_items
    monkeypatch.setattr("yeaboi.tools.azure_devops._make_azdo_clients", lambda: (client, MagicMock()))

    result = azdevops_assignee_roster("Project", days=30)

    assert len(result) == 250
    assert client.get_work_items.call_count == 2
    assert all(member["name"] != "Drive-by editor" for member in result)
    for call in client.get_work_items.call_args_list:
        assert call.kwargs["fields"] == ["System.Id", "System.AssignedTo"]
    assert "System.ChangedBy" not in client.get_work_items.call_args_list[0].kwargs["fields"]
