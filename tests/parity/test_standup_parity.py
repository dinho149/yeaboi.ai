"""Python ↔ Go parity for standup.aggregate (contracts/v1).

Both implementations run over the same synthetic inputs document; the whole
wire result must be equal (floats to 1e-9) AND every JSON object's key order
must match — object order is contractual for this method (member-keyed maps
and projected items feed the LLM prompt's json.dumps bytes). Skipped when no
``yeaboi-core`` binary is available; ``make parity`` and CI run it unskipped.

The corpus is deliberately nastier than the unit fixtures: alias collisions
and a mergeable roster dupe, unicode names, a service-hook burst plus a
custom marker, PR/merge/squash nesting with a double-sided merge commit,
AzDO ``AB#12`` and bare ``#12`` reference gates, wip sprawl, a blocked
column, a cross-standup open PR, comment churn, excused practice handles
(the sha1 handle branch included), reference-ticket relatedness suppression,
decline-streak damping, and the two-pass adjudication protocol end to end.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date

import pytest

from tests.parity._diff import approx_equal, key_orders
from yeaboi.gocore.client import CoreClient
from yeaboi.standup import aggregate
from yeaboi.standup.habits import change_handle

BINARY = os.environ.get("YEABOI_CORE_BIN") or shutil.which("yeaboi-core")

pytestmark = pytest.mark.skipif(
    not BINARY or not os.path.isfile(BINARY or ""),
    reason="yeaboi-core binary not available (run `make parity`)",
)

TODAY = date(2026, 8, 6)


@pytest.fixture
def core():
    client = CoreClient(str(BINARY))
    try:
        client.hello()
        yield client
    finally:
        client.close()


# ── Corpus ────────────────────────────────────────────────────────────────


def _item(**kw) -> dict:
    return kw


def _items() -> list[dict]:
    sonar_body = "SonarQube analysis completed. Quality gate passed. See https://sonar.corp/dashboard?id=web"
    return [
        # Ada: a ticket sitting in a Blocked column (blocker rule 1) that is
        # also discussed heavily (rule 3 — she owns it).
        _item(
            source="jira",
            author="Ada Lovelace",
            author_email="ada@corp.com",
            kind="issue",
            key="PROJ-7",
            title="updated PROJ-7 'Login redirect drops session'",
            summary="Login redirect drops session",
            status="Blocked",
            timestamp="2026-08-05T09:00:00+00:00",
            url="https://jira.corp/browse/PROJ-7",
            issue_type="Story",
        ),
        # Wip sprawl: four assigned in-progress tickets.
        *[
            _item(
                source="jira",
                author="Ada Lovelace",
                kind="wip",
                key=f"PROJ-{n}",
                title=f"In progress ticket {n}",
                summary=f"In progress ticket {n}",
                status="In Progress",
                timestamp="" if n % 2 else "2026-08-05T08:00:00+00:00",
                url=f"https://jira.corp/browse/PROJ-{n}",
            )
            for n in (2, 3, 4, 5)
        ],
        # The email closure attaches these git commits to Ada's card; the
        # subjects are deliberately low-information (commit-messages rule) and
        # name no ticket (untracked-work candidates). One is excused via its
        # sha1-subject handle.
        _item(
            source="local_git",
            author="ada@corp.com",
            kind="commit",
            key="deadbee1",
            title="quick fix",
            timestamp="2026-08-05T10:00:00+00:00",
            url="",
            repository="web",
            changed_files=["src/login.py"],
            branch="feature/login",
        ),
        _item(
            source="local_git",
            author="ada@corp.com",
            kind="commit",
            key="deadbee2",
            title="wip",
            timestamp="2026-08-05T10:05:00+00:00",
            url="",
            repository="web",
            changed_files=["src/login.py", "src/session.py"],
            branch="feature/login",
        ),
        _item(
            source="local_git",
            author="ada@corp.com",
            kind="commit",
            key="deadbee3",
            title="update stuff (#91)",
            timestamp="2026-08-05T10:10:00+00:00",
            url="",
            repository="web",
            changed_files=["src/login.py"],
            branch="feature/login",
        ),
        # A merged PR plus BOTH sides of its merge commit (same subject,
        # different SHAs) — nesting folds them, evidence dedupes to one row.
        _item(
            source="github",
            author="Ada Lovelace",
            kind="pr",
            key="#91",
            pr_id="91",
            title="Fix login redirect",
            summary="Fix login redirect",
            status="merged",
            timestamp="2026-08-05T11:00:00+00:00",
            url="https://github.com/corp/web/pull/91",
            repository="web",
            branch="feature/login",
            body="Fixes the login redirect drop. Closes PROJ-7.",
        ),
        _item(
            source="github",
            author="Ada Lovelace",
            kind="commit",
            key="feedc0de",
            title="Merge pull request #91 from corp/feature/login",
            timestamp="2026-08-05T11:01:00+00:00",
            url="https://github.com/corp/web/commit/feedc0de",
            repository="web",
        ),
        _item(
            source="github",
            author="Ada Lovelace",
            kind="commit",
            key="feedc0df",
            title="Merge pull request #91 from corp/feature/login",
            timestamp="2026-08-05T11:01:30+00:00",
            url="https://github.com/corp/web/commit/feedc0df",
            repository="web",
        ),
        # A PR that was evidence yesterday and is still open (blocker rule 2).
        _item(
            source="github",
            author="Ada Lovelace",
            kind="pr",
            key="#88",
            pr_id="88",
            title="Refactor session store",
            summary="Refactor session store",
            status="open",
            timestamp="2026-08-04T15:00:00+00:00",
            url="https://github.com/corp/web/pull/88",
            repository="web",
            branch="refactor/session",
        ),
        # Comment churn on PROJ-7: four comments from two members.
        *[
            _item(
                source="jira",
                author=author,
                kind="comment",
                key="PROJ-7",
                title=f"commented on PROJ-7 ({n})",
                timestamp=f"2026-08-05T12:0{n}:00+00:00",
                url="https://jira.corp/browse/PROJ-7",
            )
            for n, author in enumerate(["Ada Lovelace", "José Nuñez", "Ada Lovelace", "José Nuñez"])
        ],
        # José: an AzDO work item (opens the bare-#id gate) plus commits that
        # reference it via AB#12 and a 45-file large change naming PROJ-9.
        _item(
            source="azure_devops",
            author="José Nuñez",
            author_email="jose@corp.com",
            kind="work_item",
            key="#12",
            title="Harden exporter",
            summary="Harden exporter",
            status="Active",
            timestamp="2026-08-05T09:30:00+00:00",
            url="https://dev.azure.com/corp/_workitems/edit/12",
        ),
        _item(
            source="azdo_repos",
            author="jose@corp.com",
            kind="commit",
            key="beefcaf1",
            title="Fix AB#12 export flow",
            timestamp="2026-08-05T13:00:00+00:00",
            url="https://dev.azure.com/corp/_git/exporter/commit/beefcaf1",
            repository="exporter",
            changed_files=["exporter/flow.py"],
        ),
        # A 45-file PR — the large-change rule's unit is the PR, never a commit.
        _item(
            source="azdo_repos",
            author="jose@corp.com",
            kind="pr",
            key="!412",
            pr_id="412",
            title="PROJ-9 rework the pipeline layout",
            summary="Rework the pipeline layout",
            status="completed",
            timestamp="2026-08-05T13:30:00+00:00",
            url="https://dev.azure.com/corp/_git/exporter/pullrequest/412",
            repository="exporter",
            branch="feature/pipeline",
            changed_files=[f"exporter/mod_{n}.py" for n in range(45)],
            work_item_ids=["9001"],
        ),
        # José's Confluence page — documentation activity naming no ticket
        # (untracked-docs candidate, near-missable against PROJ-11).
        _item(
            source="confluence",
            author="José Nuñez",
            kind="page",
            key="DOC-1",
            title="Exporter pipeline runbook",
            summary="Exporter pipeline runbook",
            timestamp="2026-08-05T14:00:00+00:00",
            url="https://confluence.corp/x/DOC-1",
            body="How the exporter pipeline is operated day to day.",
        ),
        # Service-hook burst under Ada's identity: five near-identical Sonar
        # comments in two minutes (custom marker "wizbot" catches a sixth).
        *[
            _item(
                source="azure_devops",
                author="Ada Lovelace",
                kind="comment",
                key="PR-91",
                title=sonar_body,
                body=sonar_body,
                timestamp=f"2026-08-05T15:00:{n:02d}+00:00",
                url=f"https://dev.azure.com/corp/pr/91#c{n}",
            )
            for n in range(5)
        ],
        _item(
            source="azure_devops",
            author="José Nuñez",
            kind="comment",
            key="PR-92",
            title="wizbot scan finished for PR 92",
            timestamp="2026-08-05T15:10:00+00:00",
            url="https://dev.azure.com/corp/pr/92#c9",
        ),
        # A [bot] author (dropped by author shape, not content).
        _item(
            source="github",
            author="dep-bot[bot]",
            kind="pr",
            key="#93",
            pr_id="93",
            title="Bump lodash",
            status="open",
            timestamp="2026-08-05T16:00:00+00:00",
            url="https://github.com/corp/web/pull/93",
            repository="web",
        ),
        # An author outside the roster — the filter must drop it.
        _item(
            source="jira",
            author="Стрейнджер Иванов",
            kind="issue",
            key="X-1",
            title="Unrelated work",
            status="Done",
            timestamp="2026-08-05T17:00:00+00:00",
            url="https://jira.corp/browse/X-1",
        ),
    ]


def _reference_tickets() -> list[dict]:
    return [
        # Ada holds this open ticket; its text overlaps her loose commits so
        # relatedness can suppress or at least near-miss them.
        _item(
            source="jira",
            author="Ada Lovelace",
            kind="ticket_context",
            key="PROJ-11",
            title="PROJ-11 Quick fixes for the login flow",
            summary="Quick fixes for the login flow",
            body="Collect the small login fixes: quick fix passes on the redirect and session handling.",
            status="To Do",
            url="https://jira.corp/browse/PROJ-11",
        ),
        _item(
            source="jira",
            author="José Nuñez",
            kind="ticket_context",
            key="PROJ-14",
            title="PROJ-14 Document the exporter pipeline",
            summary="Document the exporter pipeline",
            body="Write the runbook for the exporter pipeline operations.",
            status="To Do",
            url="https://jira.corp/browse/PROJ-14",
        ),
    ]


def _previous_report() -> dict:
    return {
        "member_updates": [
            {
                "name": "Ada Lovelace",
                "summary": "Refactoring the session store; PROJ-7 in review",
                "blockers": "",
                "outlook": "Land the session refactor",
                "links": [["PROJ-7", "https://jira.corp/browse/PROJ-7"]],
                "code_links": [["#88", "https://github.com/corp/web/pull/88"]],
                "practices": [{"rule": "commit-messages"}],
            },
            {
                "name": "José Nuñez",
                "summary": "Started the exporter hardening",
                "blockers": "Waiting on infra",
                "outlook": "",
                "links": [],
                "code_links": [],
                "practices": [],
            },
        ]
    }


def _excused() -> list[list[str]]:
    excused_commit = _item(kind="commit", repository="web", title="quick fix", url="", key="")
    # key/url empty → the sha1-subject handle branch, the one that silently
    # re-accuses people if the Go hash drifts by a byte.
    return sorted(
        [
            ["commit-messages", change_handle(excused_commit)],
            ["large-change", "url:https://dev.azure.com/corp/_git/exporter/pullrequest/412"],
        ]
    )


def base_inputs(**overrides) -> dict:
    inputs = {
        "bundle": {
            "items": _items(),
            "counts": [
                ["jira", 10],
                ["azure_devops", 7],
                ["azdo_repos", 2],
                ["github", 4],
                ["local_git", 3],
                ["confluence", 1],
            ],
            "errors": [["notion", "401 unauthorized"]],
            "partial_sources": [["github", "PR file lists truncated"]],
            "skipped": [["notion", "NOTION_ROOT_PAGE_ID not set"]],
            "reference_tickets": _reference_tickets(),
        },
        "members": ["Dinho", "Ada Lovelace", "José Nuñez", "dev@corp.com"],
        "my_name": "Dinho",
        # "dev@corp.com" in the roster is the standup user under another name —
        # the merge drops it. "Me" rides along per the engine convention.
        "identity_extras": ["dinho", "dev@corp.com", "Dev Person", "Me"],
        "self_reported_names": ["Dinho"],
        "config": {"automation_handling": "exclude", "automation_markers": "wizbot", "habit_detection": "on"},
        "previous_report": _previous_report(),
        "transcript_corrections": {"José Nuñez": ["also finished the exporter retry logic"]},
        "corrected_fields": {"Ada Lovelace": ["blockers", "summary"]},
        "feedback_excused": _excused(),
        "enabled_sources": sorted(["jira", "azure_devops", "azdo_repos", "github", "local_git", "confluence"]),
        "sprint": {
            "sprint_name": "Sprint 12",
            "start_date": "2026-07-27",
            "sprint_length_weeks": 2,
            "capacity_points": 30.0,
            "completed_points": 10.0,
        },
        "history": [
            # Newest-first; a failed row, a same-date rerun and a malformed pct
            # all get skipped exactly like Python.
            {"status": "success", "standup_date": "2026-08-05", "confidence_pct": 62},
            {"status": "success", "standup_date": "2026-08-05", "confidence_pct": 99},
            {"status": "failed", "standup_date": "2026-08-04", "confidence_pct": 90},
            {"status": "success", "standup_date": "2026-08-04", "confidence_pct": 65},
            {"status": "partial", "standup_date": "2026-08-03", "confidence_pct": 70},
            {"status": "success", "standup_date": "2026-08-02", "confidence_pct": "abc"},
        ],
        "today": TODAY.isoformat(),
        "want_adjudication": False,
    }
    inputs.update(overrides)
    return json.loads(json.dumps(inputs))  # canonical JSON — what the wire carries


# ── Comparison ────────────────────────────────────────────────────────────


def _go(core: CoreClient, inputs: dict) -> dict:
    result = core.request("standup.aggregate", inputs)
    assert result.pop("contract_version", None) == 1
    return result


def _assert_match(py: dict, go: dict) -> None:
    diffs = approx_equal(py, go, "result")
    assert not diffs, "\n".join(diffs[:40])
    # Object key order is contractual (it feeds the prompt's json.dumps).
    py_orders, go_orders = key_orders(py), key_orders(go)
    order_diffs = [
        f"{path}: {py_orders.get(path)} != {go_orders.get(path)}"
        for path in sorted(set(py_orders) | set(go_orders))
        if py_orders.get(path) != go_orders.get(path)
    ]
    assert not order_diffs, "\n".join(order_diffs[:40])


class TestAggregateParity:
    def test_full_corpus_matches(self, core):
        inputs = base_inputs()
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))

    def test_corpus_exercises_the_signals_it_claims_to(self):
        """Guard the corpus itself: if a refactor mutes these, the parity run
        is no longer covering what this file says it covers."""
        result = aggregate.aggregate_standup(base_inputs())
        assert result["merged"] == ["dev@corp.com"]
        assert result["automation_notices"], "expected the sonar burst + wizbot marker to be excluded"
        assert "Ada Lovelace" in result["blocker_signals"]
        signals = "\n".join(result["blocker_signals"]["Ada Lovelace"])
        assert "PROJ-7" in signals
        assert "still open since the last standup" in signals
        rules = {s["rule"] for name in result["practices"] for s in result["practices"][name]}
        assert "wip-sprawl" in rules
        assert "large-change" not in rules, "the excused large change must stay excused"
        assert result["progress"]["confidence_trend"], "expected a usable history trend"
        ada = next(sk for sk in result["member_skeletons"] if sk["name"] == "Ada Lovelace")
        merge_rows = [
            row
            for row in ada["code"]["evidence"]
            if any(child["key"] in ("feedc0de", "feedc0df") for child in row.get("children", []))
        ]
        assert merge_rows, "expected the merge commits folded under PR #91"

    def test_without_excuses_matches_and_signals_return(self, core):
        inputs = base_inputs(feedback_excused=[])
        py = aggregate.aggregate_standup(inputs)
        _assert_match(py, _go(core, inputs))
        rules = {s["rule"] for name in py["practices"] for s in py["practices"][name]}
        assert "large-change" in rules  # un-excused now — proves the handle path is live

    def test_automation_off_matches(self, core):
        inputs = base_inputs(config={"automation_handling": "off", "habit_detection": "on"})
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))

    def test_habits_off_matches(self, core):
        inputs = base_inputs(config={"habit_detection": "off"})
        py = aggregate.aggregate_standup(inputs)
        assert py["practices"] == {}
        _assert_match(py, _go(core, inputs))

    def test_empty_bundle_matches(self, core):
        inputs = base_inputs(
            bundle={
                "items": [],
                "counts": [],
                "errors": [],
                "partial_sources": [],
                "skipped": [],
                "reference_tickets": [],
            }
        )
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))

    def test_no_members_matches(self, core):
        inputs = base_inputs(members=[], my_name="", identity_extras=[], self_reported_names=[])
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))

    def test_no_history_no_previous_report_matches(self, core):
        inputs = base_inputs(history=[], previous_report=None, transcript_corrections={}, corrected_fields={})
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))

    def test_no_capacity_matches(self, core):
        inputs = base_inputs(
            sprint={
                "sprint_name": "Sprint 12",
                "start_date": "2026-07-27",
                "sprint_length_weeks": 2,
                "capacity_points": 0.0,
                "completed_points": 0.0,
            }
        )
        py = aggregate.aggregate_standup(inputs)
        assert py["progress"]["confidence_label"] == "Insufficient data"
        _assert_match(py, _go(core, inputs))

    @pytest.mark.parametrize(
        "history",
        [
            # Improving: yesterday lower than today's number.
            [{"status": "success", "standup_date": "2026-08-05", "confidence_pct": 20}],
            # Steady: within the ±2 band.
            [{"status": "success", "standup_date": "2026-08-05", "confidence_pct": 74}],
        ],
        ids=["improving", "steady"],
    )
    def test_trend_branches_match(self, core, history):
        inputs = base_inputs(history=history)
        _assert_match(aggregate.aggregate_standup(inputs), _go(core, inputs))


class TestTwoPassAdjudicationParity:
    def test_pass_one_cases_match(self, core):
        inputs = base_inputs(want_adjudication=True)
        py = aggregate.aggregate_standup(inputs)
        go = _go(core, inputs)
        _assert_match(py, go)
        assert py["adjudication_cases"], "expected at least one near-missable loose change"

    def test_pass_two_matches_and_returns_no_cases(self, core):
        first = aggregate.aggregate_standup(base_inputs(want_adjudication=True))
        case_ids = [case["case_id"] for case in first["adjudication_cases"]]
        dropped = sorted([case_ids[0], "bogus-99"])  # junk id must cost nothing
        inputs = base_inputs(want_adjudication=True, dropped_case_ids=dropped)
        py = aggregate.aggregate_standup(inputs)
        go = _go(core, inputs)
        _assert_match(py, go)
        assert py["adjudication_cases"] == []
