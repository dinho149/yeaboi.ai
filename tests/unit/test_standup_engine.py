"""Unit tests for the standup engine pipeline (mocked LLM + sources)."""

import json
from datetime import date, datetime

import pytest

from yeaboi.agent.state import MemberUpdate, StandupGap, TranscriptClaim, TranscriptReview
from yeaboi.ops.events import OpsEvent
from yeaboi.sessions import SessionStore
from yeaboi.standup import engine
from yeaboi.standup.collector import ActivityBundle
from yeaboi.standup.sprint_context import SprintContext
from yeaboi.standup.store import StandupStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def seeded_session(db_path):
    """Create a session with a plan and return its id."""
    sid = "sess-1"
    with SessionStore(db_path) as s:
        s.create_session(sid, "Demo Project", mode="planning")
        s.save_state(sid, {"selected_team_members": ("Alice", "Bob"), "sprint_length_weeks": 2})
    return sid


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}


def _patch_common(monkeypatch, *, items, counts):
    """Patch collector, sprint_context, and token tracking for engine tests."""
    monkeypatch.setattr(
        engine.collector,
        "collect_recent_activity",
        lambda **kw: ActivityBundle(items=items, counts=counts),
    )
    monkeypatch.setattr(
        engine.sprint_context,
        "gather",
        lambda state, **kw: SprintContext(
            sprint_name="Sprint 5",
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=10,
            have_burn=True,
        ),
    )
    monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
    # Pretend the LLM provider is configured so the summarizer exercises the LLM
    # branch (individual tests override this to test the not-configured path).
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
    # Identity auto-detection is environment-dependent (global git config, live
    # tracker credentials) — stub it so tests are deterministic and offline.
    monkeypatch.setattr(engine, "_detect_tracker_identity", lambda: ("", []))
    monkeypatch.setattr(engine, "_detect_git_identity", lambda repo: [])


class TestRunStandup:
    def test_happy_path_with_llm(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Alice", "kind": "commit", "title": "login page", "source": "github"},
            {"author": "Bob", "kind": "issue", "title": "API bug", "source": "jira"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("github", 1), ("jira", 1)])
        llm_json = json.dumps(
            {
                "members": [
                    {"name": "Alice", "summary": "Built the login page", "blockers": ""},
                    {"name": "Bob", "summary": "Fixed an API bug", "blockers": "waiting on review"},
                ],
                "team_summary": "Solid progress across the board.",
            }
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))

        assert report.sprint_day == 5
        assert report.confidence_pct == 100
        assert report.confidence_label == "On track"
        assert report.team_summary == "Solid progress across the board."
        names = {m.name: m for m in report.member_updates}
        assert names["Bob"].blockers == "waiting on review"
        assert all(m.source == "inferred" for m in report.member_updates)
        assert report.activity_counts == (("github", 1), ("jira", 1))

    def test_self_report_is_context_not_replacement(self, monkeypatch, db_path, seeded_session):
        """A typed update rides alongside the activity analysis — it never suppresses it."""
        _patch_common(
            monkeypatch,
            items=[
                {"author": "Alice", "kind": "commit", "title": "auth pairing session", "source": "github"},
                {"author": "Bob", "kind": "issue", "title": "x", "source": "jira"},
            ],
            counts=[("github", 1), ("jira", 1)],
        )
        with StandupStore(db_path) as store:
            store.save_my_update(seeded_session, "2026-07-10", "Alice", "I paired with Bob on auth all day.")
        llm_json = json.dumps(
            {
                "members": [
                    {"name": "Alice", "summary": "Paired on auth; pushed the pairing-session commit."},
                    {"name": "Bob", "summary": "Worked on x"},
                ],
                "team_summary": "ok",
            }
        )
        captured: dict = {}

        def _fake_invoke(self, m):
            captured["prompt"] = m
            return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": _fake_invoke})())

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        # Analysis of her activity, with her own words carried separately.
        assert alice.summary == "Paired on auth; pushed the pairing-session commit."
        assert alice.self_report == "I paired with Bob on auth all day."
        assert alice.source == "combined"
        # Her self-report reached the LLM as context (Alice's payload entry).
        assert "I paired with Bob on auth all day." in str(captured["prompt"])

    def test_self_report_without_activity(self, monkeypatch, db_path, seeded_session):
        """A self-reporter with no matching activity still surfaces, tagged self-reported."""
        _patch_common(
            monkeypatch,
            items=[{"author": "Bob", "kind": "issue", "title": "x", "source": "jira"}],
            counts=[("jira", 1)],
        )
        with StandupStore(db_path) as store:
            store.save_my_update(seeded_session, "2026-07-10", "Alice", "Interviews all day.")
        llm_json = json.dumps({"members": [{"name": "Bob", "summary": "Worked on x"}], "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.source == "self-reported"
        assert alice.self_report == "Interviews all day."
        assert alice.summary == "Interviews all day."

    def test_pasted_update_images_reach_llm(self, monkeypatch, db_path, seeded_session, tmp_path):
        """Screenshots saved with 'My Update' become image blocks on the summary call."""
        img = tmp_path / "burndown.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        _patch_common(
            monkeypatch,
            items=[{"author": "Bob", "kind": "issue", "title": "x", "source": "jira"}],
            counts=[("jira", 1)],
        )
        with StandupStore(db_path) as store:
            store.save_my_update(seeded_session, "2026-07-10", "Alice", "chart attached", images=[str(img)])
        llm_json = json.dumps({"members": [{"name": "Bob", "summary": "x"}], "team_summary": "ok"})
        sent = {}

        class _L:
            def invoke(self, messages):
                sent["content"] = messages[0].content
                return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: _L())

        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        content = sent["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"

    def test_llm_failure_falls_back(self, monkeypatch, db_path, seeded_session):
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "did work", "source": "github"}],
            counts=[("github", 1)],
        )

        def boom(self, m):
            raise RuntimeError("timeout")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": boom})())
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        # Fallback: Alice's summary is her activity title.
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert "did work" in alice.code_summary
        assert report.team_summary  # deterministic team summary present

    def test_auth_error_becomes_warning(self, monkeypatch, db_path, seeded_session):
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}],
            counts=[("github", 1)],
        )

        import anthropic

        def boom(self, m):
            raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": boom})())
        # No longer raises — surfaces a warning and falls back deterministically.
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("API key invalid" in w for w in report.warnings)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert "x" in alice.code_summary  # deterministic fallback used

    def test_ollama_model_missing_becomes_pull_hint_warning(self, monkeypatch, db_path, seeded_session):
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}],
            counts=[("github", 1)],
        )
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")

        def boom(self, m):
            raise RuntimeError("model 'qwen3:8b' not found, try pulling it first")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": boom})())
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("ollama pull qwen3:8b" in w for w in report.warnings)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert "x" in alice.code_summary  # deterministic fallback still used

    def test_no_api_key_warns(self, monkeypatch, db_path, seeded_session):
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "shipped x", "source": "github"}],
            counts=[("github", 1)],
        )
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "ANTHROPIC_API_KEY not set"))

        # get_llm should never be called when the provider isn't configured.
        def _should_not_call(**k):
            raise AssertionError("LLM must not be invoked when unconfigured")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _should_not_call)
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("ANTHROPIC_API_KEY not set" in w for w in report.warnings)

    def test_source_auth_error_surfaces_as_warning(self, monkeypatch, db_path, seeded_session):
        # Collector reports a source auth error → it appears in report.warnings.
        from yeaboi.standup.collector import ActivityBundle

        monkeypatch.setattr(
            engine.collector,
            "collect_recent_activity",
            lambda **kw: ActivityBundle(items=[], counts=[], errors=[("jira", "authentication failed — check token")]),
        )
        monkeypatch.setattr(
            engine.sprint_context,
            "gather",
            lambda state, **kw: __import__("yeaboi.standup.sprint_context", fromlist=["SprintContext"]).SprintContext(),
        )
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        llm_json = json.dumps(
            {"members": [{"name": "Alice", "summary": "x"}, {"name": "Bob", "summary": "y"}], "team_summary": "ok"}
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("Jira: authentication failed" in w for w in report.warnings)

    def test_auto_exports_md_and_html(self, monkeypatch, db_path, seeded_session, tmp_path):
        _patch_common(monkeypatch, items=[], counts=[])
        monkeypatch.setattr("yeaboi.paths.STANDUP_EXPORTS_DIR", tmp_path / "exports" / "standup")
        llm_json = json.dumps(
            {"members": [{"name": "Alice", "summary": "x"}, {"name": "Bob", "summary": "y"}], "team_summary": "ok"}
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        # A dated .md + .html were written under the standup exports dir.
        exports = list((tmp_path / "exports" / "standup").rglob("standup-2026-07-10.*"))
        assert {p.suffix for p in exports} == {".md", ".html"}

    def test_records_run_to_history(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])
        # No members with activity and no self-reports besides roster → LLM still called for inferred roster.
        llm_json = json.dumps(
            {
                "members": [{"name": "Alice", "summary": "quiet day"}, {"name": "Bob", "summary": "quiet day"}],
                "team_summary": "quiet",
            }
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        with StandupStore(db_path) as store:
            latest = store.get_latest_report(seeded_session)
            history = store.get_history(seeded_session)
        assert latest is not None
        assert len(history) == 1

    def test_delivery_invoked_when_enabled(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])
        llm_json = json.dumps(
            {"members": [{"name": "Alice", "summary": "x"}, {"name": "Bob", "summary": "y"}], "team_summary": "ok"}
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

        delivered = {}

        def fake_deliver(dispatch, channels):
            delivered["channels"] = channels
            delivered["dispatch"] = dispatch
            return {c: True for c in channels}

        # Patched on the real module rather than standup/delivery.py's shim: the
        # shim re-exports the object, so rebinding a name there is invisible to
        # anything that imports from ceremonies.delivery directly.
        import yeaboi.ceremonies.delivery as delivery_mod

        monkeypatch.setattr(delivery_mod, "deliver", fake_deliver)
        engine.run_standup(
            seeded_session, deliver=True, channels=["terminal"], db_path=db_path, today=date(2026, 7, 10)
        )
        assert delivered["channels"] == ["terminal"]
        # The channels take a Dispatch now, carrying the standup's own plaintext
        # rendering rather than a re-wording of it.
        assert "Daily Standup" in delivered["dispatch"].title


class TestAutomationFilter:
    """Service-hook activity posted under a member's identity is excluded, with a notice."""

    @staticmethod
    def _wiz_items(author="Alice", n=18):
        repos = ["infra", "onboarding", "credit-risk", "security", "pricing", "automation"]
        return [
            {
                "author": author,
                "kind": "review",
                "title": f"reviewed PR !{i}: deploy ({repos[i % 6]})",
                "body": (
                    f"Security finding: publicly exposed storage container detected in "
                    f"{repos[i % 6]}/infra/main.tf line {i}. Severity: High. Review before merging."
                ),
                "timestamp": f"2026-07-10T09:00:{i:02d}",
                "key": f"review-comment-{i}",
                "repository": repos[i % 6],
                "source": "azure_devops",
            }
            for i in range(n)
        ]

    def _run(self, monkeypatch, db_path, seeded_session, *, handling=None):
        items = [
            *self._wiz_items(),
            {"author": "Alice", "kind": "commit", "title": "real fix", "source": "github"},
            {"author": "Bob", "kind": "issue", "title": "API bug", "source": "jira"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("azure_devops", 18), ("github", 1), ("jira", 1)])
        # Deterministic fallback path — no LLM mocking needed for count assertions.
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        if handling is not None:
            with StandupStore(db_path) as store:
                store.save_config(
                    seeded_session,
                    enabled=False,
                    time="10:00",
                    weekdays="1-5",
                    delivery_channels=["terminal"],
                    automation_handling=handling,
                )
        return engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))

    def test_burst_excluded_from_counts_and_credit(self, monkeypatch, db_path, seeded_session):
        report = self._run(monkeypatch, db_path, seeded_session)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.code_activity_count == 1  # only the genuine commit
        assert dict(report.activity_counts) == {"azure_devops": 0, "github": 1, "jira": 1}
        notice = next(w for w in report.warnings if w.startswith("Excluded"))
        assert "18 near-identical review item(s) posted under 'Alice'" in notice
        assert "across 6 repositories" in notice

    def test_handling_off_keeps_everything(self, monkeypatch, db_path, seeded_session):
        report = self._run(monkeypatch, db_path, seeded_session, handling="off")
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.code_activity_count == 19  # 18 hook comments + 1 commit
        assert dict(report.activity_counts)["azure_devops"] == 18
        assert not any(w.startswith("Excluded") for w in report.warnings)

    def test_custom_marker_from_config(self, monkeypatch, db_path, seeded_session):
        items = [
            {
                "author": "Alice",
                "kind": "review",
                "title": "reviewed PR !1: deploy",
                "body": "AcmeScan flagged a finding in this change.",
                "timestamp": "2026-07-10T09:00:00",
                "key": "rc-1",
                "repository": "infra",
                "source": "azure_devops",
            }
        ]
        _patch_common(monkeypatch, items=items, counts=[("azure_devops", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        with StandupStore(db_path) as store:
            store.save_config(
                seeded_session,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                automation_markers="acmescan",
            )
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert dict(report.activity_counts)["azure_devops"] == 0
        assert any("matched 'acmescan'" in w for w in report.warnings)

    def test_rebuild_bundle_preserves_partial_sources(self):
        # Regression: the roster-filter rebuild used to drop partial_sources,
        # silently swallowing partial-coverage warnings.
        bundle = ActivityBundle(
            items=[{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}],
            counts=[("github", 1)],
            errors=[("jira", "401")],
            skipped=[("notion", "not configured")],
            partial_sources=[("azure_devops", "truncated after 100 PRs")],
        )
        rebuilt = engine._rebuild_bundle(bundle, bundle.items)
        assert rebuilt.partial_sources == [("azure_devops", "truncated after 100 PRs")]
        assert rebuilt.errors == [("jira", "401")]
        assert rebuilt.skipped == [("notion", "not configured")]

    def test_roster_filter_preserves_partial_sources(self):
        bundle = ActivityBundle(
            items=[{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}],
            counts=[("github", 1)],
            partial_sources=[("azure_devops", "truncated after 100 PRs")],
        )
        filtered = engine._filter_bundle_to_members(bundle, {"Alice": {"alice"}})
        assert filtered.partial_sources == [("azure_devops", "truncated after 100 PRs")]
        assert len(filtered.items) == 1


class TestAliasMatching:
    def test_normalize_author_case_and_strip(self):
        assert engine._normalize_author("  Alice ") == {"alice"}

    def test_normalize_author_email_adds_local_part(self):
        assert engine._normalize_author("Omar@X.com") == {"omar@x.com", "omar"}

    def test_normalize_author_empty(self):
        assert engine._normalize_author("") == set()
        assert engine._normalize_author(None) == set()

    def test_build_alias_map_names_always_included(self):
        m = engine._build_alias_map(["Alice", "Bob"])
        assert m["Alice"] == {"alice"}
        assert m["Bob"] == {"bob"}

    def test_build_alias_map_my_aliases_and_git_identity(self, monkeypatch):
        monkeypatch.setattr(engine, "_detect_git_identity", lambda repo: ["Omar Noureldin", "omar@x.com"])
        m = engine._build_alias_map(["Me", "Bob"], my_name="Me", my_aliases="omardin14, Omar N", repo_path="/some/repo")
        assert {"me", "omardin14", "omar n", "omar noureldin", "omar@x.com", "omar"} <= m["Me"]
        assert m["Bob"] == {"bob"}  # only the standup user gets extra aliases

    def test_detect_git_identity_no_git(self, monkeypatch):
        """No git binary at all → no identities, no crash (repo and global lookups)."""
        import subprocess

        def no_git(*a, **k):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", no_git)
        assert engine._detect_git_identity("") == []
        assert engine._detect_git_identity("/some/repo") == []

    def test_detect_git_identity_includes_global(self, monkeypatch):
        """With no repo path the GLOBAL git identity is still detected (zero-config)."""
        import subprocess
        from types import SimpleNamespace

        def fake_run(cmd, **k):
            assert "--global" in cmd
            value = "Omar Din" if cmd[-1] == "user.name" else "omar@x.com"
            return SimpleNamespace(returncode=0, stdout=value + "\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert engine._detect_git_identity("") == ["Omar Din", "omar@x.com"]

    def test_detect_tracker_identity_from_jira(self, monkeypatch):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.myself.return_value = {"displayName": "Omar Din", "emailAddress": "omar@x.com"}
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: client)
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        display, identities = engine._detect_tracker_identity()
        assert display == "Omar Din"
        assert identities == ["Omar Din", "omar@x.com"]

    def test_detect_tracker_identity_unconfigured(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        assert engine._detect_tracker_identity() == ("", [])

    def test_grouping_via_alias(self):
        items = [
            {"author": "omardin14", "kind": "commit", "title": "fix login", "source": "github"},
            {"author": "Bob", "kind": "issue", "title": "API bug", "source": "jira"},
            {"author": "stranger", "kind": "commit", "title": "misc", "source": "github"},
        ]
        alias_map = {"Me": {"me", "omardin14"}, "Bob": {"bob"}}
        grouped = engine._group_activity_by_author(items, ["Me", "Bob"], alias_map)
        assert [a["title"] for a in grouped["Me"]] == ["fix login"]
        assert [a["title"] for a in grouped["Bob"]] == ["API bug"]

    def test_grouping_case_insensitive_without_alias_map(self):
        items = [{"author": "ALICE", "kind": "commit", "title": "x", "source": "github"}]
        grouped = engine._group_activity_by_author(items, ["Alice"])
        assert len(grouped["Alice"]) == 1


class TestRosterMerge:
    def _llm(self, monkeypatch, members_json):
        llm_json = json.dumps({"members": members_json, "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

    def test_unmatched_authors_are_excluded(self, monkeypatch, db_path, seeded_session):
        """The authoritative roster excludes outsider cards and activity totals."""
        _patch_common(
            monkeypatch,
            items=[
                {"author": "Alice", "kind": "commit", "title": "login", "source": "github"},
                {"author": "charlie-dev", "kind": "pr", "title": "refactor", "source": "github"},
            ],
            counts=[("github", 2)],
        )
        self._llm(monkeypatch, [{"name": "Alice", "summary": "login"}, {"name": "charlie-dev", "summary": "refactor"}])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = [m.name for m in report.member_updates]
        assert "charlie-dev" not in names
        assert report.activity_counts == (("github", 1),)
        assert "Alice" in names and "Bob" in names

    def test_saved_tracker_and_member_scope_drive_collection(self, monkeypatch, db_path, seeded_session):
        with StandupStore(db_path) as store:
            store.save_config(
                seeded_session,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                tracker_sources=["jira"],
                team_members=["Alice"],
                roster_configured=True,
            )
            store.save_my_update(seeded_session, "2026-07-10", "Bob", "This must stay outside the selected team.")
        items = [
            {"author": "Alice", "kind": "issue", "title": "login", "source": "jira"},
            {"author": "Bob", "kind": "issue", "title": "outsider", "source": "azure_devops"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 1), ("azure_devops", 1)])
        monkeypatch.setattr(
            engine,
            "_resolve_source_params",
            lambda config: {
                "jira_project": "PSOT",
                "azdo_project": "Core",
                "github_repo": "",
                "local_repo_path": "",
                "confluence_space": "",
                "notion_root": "",
            },
        )
        captured = {}

        def _collect(**kwargs):
            captured["sources"] = kwargs["sources"]
            return ActivityBundle(items=items, counts=[("jira", 1)])

        monkeypatch.setattr(engine.collector, "collect_recent_activity", _collect)
        self._llm(monkeypatch, [{"name": "Alice", "summary": "login"}])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert engine.collector.SOURCE_JIRA in captured["sources"]
        assert engine.collector.SOURCE_AZDO not in captured["sources"]
        assert [member.name for member in report.member_updates] == ["Me", "Alice"]
        assert report.activity_counts == (("jira", 1),)

    def test_my_activity_attaches_via_configured_alias(self, monkeypatch, db_path, seeded_session):
        """Aliased GitHub commits fold into the standup user's card, not a stranger card."""
        with StandupStore(db_path) as store:
            store.save_config(
                seeded_session,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                my_aliases="omardin14",
            )
        _patch_common(
            monkeypatch,
            items=[{"author": "omardin14", "kind": "commit", "title": "fix login", "source": "github"}],
            counts=[("github", 1)],
        )
        monkeypatch.setattr(engine, "_detect_git_identity", lambda repo: [])
        self._llm(monkeypatch, [{"name": "Me", "summary": "Fixed the login flow."}])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = [m.name for m in report.member_updates]
        assert "omardin14" not in names  # claimed by "Me" via the alias
        me = next(m for m in report.member_updates if m.name == "Me")
        assert me.summary == "Fixed the login flow."
        assert me.source == "inferred"

    def test_no_sources_configured_warns(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("No activity sources configured" in w for w in report.warnings)


class TestActivityWindow:
    def _llm_ok(self, monkeypatch):
        llm_json = json.dumps({"members": [], "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

    def test_default_window_is_previous_working_day(self, monkeypatch, db_path, seeded_session):
        """A Monday run reaches back to Friday 00:00 — weekend work windows never skip Friday."""
        captured: dict = {}

        def fake_collect(**kw):
            captured.update(kw)
            return ActivityBundle(items=[], counts=[("jira", 0)])

        monkeypatch.setattr(engine.collector, "collect_recent_activity", fake_collect)
        monkeypatch.setattr(
            engine.sprint_context,
            "gather",
            lambda state, **kw: SprintContext(sprint_name="S", start_date="2026-07-06", sprint_length_weeks=2),
        )
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        self._llm_ok(monkeypatch)

        # 2026-07-20 is a Monday → window start must be Friday 2026-07-17 00:00.
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 20))
        since = captured["since"]
        assert (since.year, since.month, since.day, since.hour) == (2026, 7, 17, 0)
        assert "days" not in captured
        assert report.activity_window.startswith("Fri 2026-07-17")
        assert report.activity_window.endswith("→ now")
        # Machine-readable bounds for the timeline axis: tz-aware ISO, start
        # at the window's midnight, end stamped at collection time.
        start = datetime.fromisoformat(report.activity_window_start)
        end = datetime.fromisoformat(report.activity_window_end)
        assert (start.year, start.month, start.day, start.hour) == (2026, 7, 17, 0)
        assert start.tzinfo is not None and end.tzinfo is not None
        assert start < end

    def test_explicit_days_keeps_legacy_window(self, monkeypatch, db_path, seeded_session):
        captured: dict = {}

        def fake_collect(**kw):
            captured.update(kw)
            return ActivityBundle(items=[], counts=[])

        monkeypatch.setattr(engine.collector, "collect_recent_activity", fake_collect)
        monkeypatch.setattr(
            engine.sprint_context,
            "gather",
            lambda state, **kw: SprintContext(sprint_name="S", start_date="2026-07-06", sprint_length_weeks=2),
        )
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        self._llm_ok(monkeypatch)

        report = engine.run_standup(seeded_session, deliver=False, days=3, db_path=db_path, today=date(2026, 7, 20))
        assert captured["days"] == 3
        assert "since" not in captured
        assert report.activity_window == "last 3 day(s)"
        start = datetime.fromisoformat(report.activity_window_start)
        end = datetime.fromisoformat(report.activity_window_end)
        assert start.tzinfo is not None and end.tzinfo is not None
        assert (end - start).days == 3


class TestIdentityResolution:
    def _llm(self, monkeypatch, members_json):
        llm_json = json.dumps({"members": members_json, "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

    def test_me_resolves_to_tracker_display_name(self, monkeypatch, db_path, seeded_session):
        """Default "Me" + detected Jira identity → one card under the real name.

        The self-report typed as "Me" is re-keyed, activity authored under the
        Jira displayName attaches to that same card, and no duplicate
        "Omar Din" member appears.
        """
        _patch_common(
            monkeypatch,
            items=[{"author": "Omar Din", "kind": "issue", "title": "GuardDuty S3", "source": "jira"}],
            counts=[("jira", 1)],
        )
        monkeypatch.setattr(engine, "_detect_tracker_identity", lambda: ("Omar Din", ["Omar Din", "omar@x.com"]))
        with StandupStore(db_path) as store:
            store.save_my_update(seeded_session, "2026-07-10", "Me", "Working on GuardDuty.")
        self._llm(monkeypatch, [{"name": "Omar Din", "summary": "Progressing GuardDuty S3 protection."}])

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = [m.name for m in report.member_updates]
        assert "Me" not in names
        assert names.count("Omar Din") == 1
        assert names[0] == "Omar Din"  # the user's card comes first
        me = report.member_updates[0]
        assert me.self_report == "Working on GuardDuty."
        assert me.summary == "Progressing GuardDuty S3 protection."
        assert me.source == "combined"
        assert report.my_name == "Omar Din"

    def test_explicit_user_name_not_renamed(self, monkeypatch, db_path, seeded_session):
        """STANDUP_USER_NAME set → keep it, but detected identities still alias-match."""
        _patch_common(
            monkeypatch,
            items=[{"author": "Omar Din", "kind": "issue", "title": "x", "source": "jira"}],
            counts=[("jira", 1)],
        )
        monkeypatch.setattr("yeaboi.config.get_standup_user_name", lambda: "Dinho")
        monkeypatch.setattr(engine, "_detect_tracker_identity", lambda: ("Omar Din", ["Omar Din"]))
        self._llm(monkeypatch, [{"name": "Dinho", "summary": "Worked on x."}])

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = [m.name for m in report.member_updates]
        assert names[0] == "Dinho"
        assert "Omar Din" not in names  # aliased into Dinho's card, not a stranger card
        assert report.my_name == "Dinho"

    def test_roster_from_tracker_when_no_plan_members(self, monkeypatch, db_path):
        """No plan roster → teammates come from Jira/AzDO assignees (fetch_roster),
        including those with no activity in today's window."""
        sid = "sess-roster"
        with SessionStore(db_path) as s:
            s.create_session(sid, "Roster Project", mode="planning")
            s.save_state(sid, {"sprint_length_weeks": 2})  # no selected_team_members
        _patch_common(
            monkeypatch,
            items=[{"author": "Sarah", "kind": "issue", "title": "YEA-42 review", "source": "jira"}],
            counts=[("jira", 1)],
        )
        monkeypatch.setattr(engine, "_detect_tracker_identity", lambda: ("Omar Din", ["Omar Din"]))

        monkeypatch.setattr(
            "yeaboi.standup.roster.discover_team_members",
            lambda *a, **kw: ["James", "Omar Din", "Sarah"],
        )
        llm_json = json.dumps(
            {"members": [{"name": "Sarah", "summary": "Moved YEA-42 into review."}], "team_summary": "ok"}
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

        report = engine.run_standup(sid, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = [m.name for m in report.member_updates]
        # User first, whole team present, the roster's "Omar Din" merged into the user's card.
        assert names == ["Omar Din", "James", "Sarah"]
        james = next(m for m in report.member_updates if m.name == "James")
        assert james.summary == "No activity detected."
        sarah = next(m for m in report.member_updates if m.name == "Sarah")
        assert sarah.summary == "Moved YEA-42 into review."


class TestProgressCallback:
    def test_phases_reported_in_order(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[("jira", 0)])
        llm_json = json.dumps({"members": [], "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )
        phases: list[str] = []
        engine.run_standup(
            seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10), on_progress=phases.append
        )
        assert phases == [
            # The transcript sweep runs first so yesterday's corrections are in
            # hand before today's activity is collected and summarised.
            "Reviewing meeting transcripts",
            "Collecting recent activity",
            "Reading sprint progress",
            "Resolving team & identities",
            # The deterministic aggregate (the standup.aggregate seam) gets its
            # own phase — with the sidecar it's a visible hop, without it the
            # same work just used to hide inside the summary phase.
            "Scoring activity & practices",
            "Writing summaries with AI",
            "Saving & exporting",
        ]

    def test_broken_callback_never_breaks_the_run(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])

        def boom(phase):
            raise RuntimeError("ui went away")

        report = engine.run_standup(
            seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10), on_progress=boom
        )
        assert report is not None


class TestAliasEnrichment:
    def test_email_seen_on_tracker_item_claims_git_commits(self):
        """A Jira item exposing a member's email lets their git commits attach."""
        alias_map = {"Omar Din": {"omar din"}, "Ahmet Ince": {"ahmet ince"}}
        items = [
            {"author": "Omar Din", "author_email": "omar.din@corp.com", "kind": "issue", "title": "t"},
            {"author": "omar.din@corp.com", "author_email": "omar.din@corp.com", "kind": "commit", "title": "c"},
        ]
        engine._enrich_aliases_from_items(alias_map, items)
        assert "omar.din@corp.com" in alias_map["Omar Din"]
        assert "omar.din" in alias_map["Omar Din"]  # local part too
        assert alias_map["Ahmet Ince"] == {"ahmet ince"}  # untouched

        grouped = engine._group_activity_by_author(items, list(alias_map), alias_map)
        assert len(grouped["Omar Din"]) == 2

    def test_no_emails_changes_nothing(self):
        alias_map = {"Alice": {"alice"}}
        engine._enrich_aliases_from_items(alias_map, [{"author": "Alice", "kind": "issue", "title": "t"}])
        assert alias_map == {"Alice": {"alice"}}

    def test_run_standup_does_not_spawn_phantom_member_for_known_email(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Alice", "author_email": "alice@corp.com", "kind": "issue", "title": "t", "source": "jira"},
            {
                "author": "alice@corp.com",
                "author_email": "alice@corp.com",
                "kind": "commit",
                "title": "c",
                "source": "local_git",
            },
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 1), ("local_git", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        report = engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        names = [m.name for m in report.member_updates]
        assert "alice@corp.com" not in names
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert "c" in alice.summary or "t" in alice.summary


class TestWipFlow:
    def test_wip_only_member_reads_continuing_work(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Bob", "kind": "wip", "title": "Ship exports", "status": "In Progress", "source": "jira"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        report = engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        bob = next(m for m in report.member_updates if m.name == "Bob")
        assert bob.summary == "Continuing work on: Ship exports"

    def test_truly_empty_member_still_no_activity(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[("jira", 0)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        report = engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.summary == "No activity detected."

    def test_fresh_activity_preferred_over_wip(self):
        acts = [
            {"kind": "wip", "title": "Old ticket"},
            {"kind": "commit", "title": "shipped fix"},
        ]
        assert engine._fallback_summary(acts) == "shipped fix"

    def test_fallback_summary_is_a_headline_not_a_wall(self):
        # The summary renders as the card's headline: two titles + a count,
        # with the rest left to the category summaries and evidence rows.
        acts = [{"kind": "commit", "title": f"change {i}"} for i in range(5)]
        assert engine._fallback_summary(acts) == "change 0; change 1; and 3 more"
        assert engine._fallback_summary(acts[:2]) == "change 0; change 1"

    def test_strip_rationale_echo_drops_the_restated_opener_only(self):
        # Real-run shape: the LLM opens the team summary by rewording the
        # confidence rationale shown two lines above it.
        rationale = "Day 2 of 10: 0 of ~3 ideal points burned (0%)."
        summary = (
            "The sprint is on day 2 of 10 with no points burned yet, putting the team behind the "
            "ideal burn curve. Nikolai delivered the most concrete output, merging a substantial "
            "Jenkins governance branch."
        )
        assert engine._strip_rationale_echo(summary, rationale) == (
            "Nikolai delivered the most concrete output, merging a substantial Jenkins governance branch."
        )

    def test_strip_rationale_echo_keeps_a_sentence_with_its_own_content(self):
        rationale = "Day 2 of 10: 0 of ~3 ideal points burned (0%)."
        summary = "Two of six members show no activity on day 2, which is a risk worth surfacing."
        assert engine._strip_rationale_echo(summary, rationale) == summary

    def test_strip_rationale_echo_ignores_a_rationale_too_short_to_match_on(self):
        assert engine._strip_rationale_echo("Behind pace.", "Behind.") == "Behind pace."

    def test_fallback_team_summary_does_not_restate_chip_or_details(self):
        # The confidence chip carries the label+rationale and the Details footer
        # carries the per-source counts; the fallback must not render them twice.
        from yeaboi.standup import confidence

        bundle = ActivityBundle(items=[{"kind": "commit", "title": "x"}], counts=[("jira", 1)])
        progress = confidence.SprintProgress(
            confidence_label="Behind", confidence_rationale="Day 2 of 10: 0 of ~3 ideal points burned (0%)."
        )
        summary = engine._build_fallback_team_summary(bundle, progress)
        assert summary == "Sprint status: Behind."
        empty = engine._build_fallback_team_summary(ActivityBundle(), progress)
        assert "No activity detected" in empty

    def test_llm_payload_splits_activity_and_in_progress(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Alice", "kind": "commit", "title": "login page", "source": "github"},
            {"author": "Alice", "kind": "wip", "title": "Ship exports", "status": "In Progress", "source": "jira"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("github", 1), ("jira", 1)])
        captured: dict = {}

        def fake_prompt(**kwargs):
            captured.update(kwargs)
            return "PROMPT"

        monkeypatch.setattr("yeaboi.prompts.standup.get_standup_summary_prompt", fake_prompt)
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_with_images", lambda llm, prompt, images: _FakeResp('{"members": []}')
        )
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **kw: object())
        engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        alice = next(m for m in captured["members"] if m["name"] == "Alice")
        assert [a["title"] for a in alice["code_activity"]] == ["login page"]
        assert [a["title"] for a in alice["in_progress"]] == ["Ship exports"]

    def test_llm_payload_strips_urls_and_keys(self, monkeypatch, db_path, seeded_session):
        items = [
            {
                "author": "Alice",
                "kind": "commit",
                "title": "login page",
                "source": "github",
                "key": "abc123",
                "url": "https://github.com/o/r/commit/abc123",
            },
        ]
        _patch_common(monkeypatch, items=items, counts=[("github", 1)])
        captured: dict = {}

        def fake_prompt(**kwargs):
            captured.update(kwargs)
            return "PROMPT"

        monkeypatch.setattr("yeaboi.prompts.standup.get_standup_summary_prompt", fake_prompt)
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_with_images", lambda llm, prompt, images: _FakeResp('{"members": []}')
        )
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **kw: object())
        engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        alice = next(m for m in captured["members"] if m["name"] == "Alice")
        item = alice["code_activity"][0]
        # Rendering-only fields must not spend prompt tokens.
        assert not {"url", "key", "summary", "pr_id", "branch", "timestamp"} & set(item)

    def test_confidence_excludes_wip_from_activity_count(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Alice", "kind": "commit", "title": "c", "source": "github"},
            {"author": "Bob", "kind": "wip", "title": "w", "source": "jira"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("github", 1), ("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        seen: dict = {}
        real_compute = engine.confidence.compute

        def spy_compute(**kwargs):
            seen.update(kwargs)
            return real_compute(**kwargs)

        monkeypatch.setattr(engine.confidence, "compute", spy_compute)
        engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        assert seen["activity_count"] == 1


class TestSkippedSourceReasons:
    """``_skipped_sources`` is the only place that can tell the three cases apart."""

    def _params(self, **overrides):
        params = {
            "jira_project": "",
            "azdo_project": "",
            "confluence_space": "",
            "notion_root": "",
            "github_repo": "",
            "local_repo_path": "",
            "github_repositories": [],
            "azdo_projects": [],
            "azdo_repositories": [],
        }
        params.update(overrides)
        return params

    def test_connected_but_unticked_reads_as_a_choice(self, monkeypatch):
        # A GitHub token is present, so this is two keypresses in the picker — not
        # a .env problem, and not worth a notice.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_x")
        skipped, unmet = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [])
        assert dict(skipped)["github"] == "not selected in setup"
        assert "github" not in unmet

    def test_unticked_and_unconfigured_names_the_env_var(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, unmet = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [])
        assert dict(skipped)["github"] == "GITHUB_TOKEN not set"
        assert "github" not in unmet

    def test_ticked_but_unreachable_is_unmet(self, monkeypatch):
        # Asked for and not delivered — the one case that earns a ⚠ notice.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, unmet = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], ["github"], [])
        assert dict(skipped)["github"] == "GITHUB_TOKEN not set"
        assert "github" in unmet

    def test_empty_scope_says_so_rather_than_blaming_credentials(self, monkeypatch):
        # The user ticked GitHub and chose no repos; _resolve_code_scope stripped it.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_x")
        skipped, unmet = engine._skipped_sources(
            self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [], ["github"]
        )
        assert dict(skipped)["github"] == "selected, but no organisations or repositories in scope"
        assert "github" in unmet

    def test_a_source_that_ran_is_never_listed(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_x")
        skipped, _ = engine._skipped_sources(
            self._params(jira_project="PROJ", github_repositories=["o/r"]),
            {"jira", "github"},
            ["jira"],
            ["github"],
            [],
        )
        assert "github" not in dict(skipped)

    def test_azure_tickets_and_code_report_once_when_neither_was_asked_for(self, monkeypatch):
        # They share one .env block; two lines for one missing integration is noise.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, _ = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [])
        assert "azure_devops" in dict(skipped)
        assert "azdo_repos" not in dict(skipped)

    def test_the_deduped_azure_row_says_it_covers_both_surfaces(self, monkeypatch):
        # The surviving row is labelled "Azure DevOps tickets", so without this the
        # reader is left wondering what happened to the code half.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, _ = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [])
        assert dict(skipped)["azure_devops"] == "AZURE_DEVOPS_PROJECT not set — tickets and code"

    def test_azure_row_is_not_annotated_when_code_reports_separately(self, monkeypatch):
        # Nothing was deduped here, so the qualifier would be a lie.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, _ = engine._skipped_sources(
            self._params(jira_project="PROJ"), {"jira"}, ["jira"], ["azure_devops"], []
        )
        assert dict(skipped)["azdo_repos"] == "AZURE_DEVOPS_PROJECT not set"
        assert "— tickets and code" not in dict(skipped)["azure_devops"]

    def test_a_deliberate_non_choice_is_listed_but_never_chased(self, monkeypatch):
        # The whole point of the (skipped, unmet) split: someone with no local repo
        # must not read "Not scanned: Local Git" at the bottom of every standup forever.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, unmet = engine._skipped_sources(self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], [])
        assert dict(skipped)["local_git"] == "no repo path configured"
        assert "local_git" not in unmet

    def test_documentation_sources_classify_like_the_rest(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        # Confluence is connected but unticked; Notion is neither.
        skipped, unmet = engine._skipped_sources(
            self._params(jira_project="PROJ", confluence_space="ENG"), {"jira"}, ["jira"], [], []
        )
        assert dict(skipped)["confluence"] == "not selected in setup"
        assert dict(skipped)["notion"] == "NOTION_ROOT_PAGE_ID not set"
        assert not {"confluence", "notion"} & unmet

    def test_a_ticked_documentation_source_that_cannot_run_is_unmet(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, unmet = engine._skipped_sources(
            self._params(jira_project="PROJ"), {"jira"}, ["jira"], [], ["confluence"]
        )
        assert dict(skipped)["confluence"] == "CONFLUENCE_SPACE_KEY not set"
        assert "confluence" in unmet

    def test_azure_code_reports_separately_when_it_was_asked_for(self, monkeypatch):
        # Tickets ran, code did not: "Azure DevOps" alone would be ambiguous.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        skipped, unmet = engine._skipped_sources(
            self._params(jira_project="PROJ", azdo_project="P"),
            {"jira", "azure_devops"},
            ["jira", "azure_devops"],
            [],
            [],
            ["azure_devops"],
        )
        assert dict(skipped)["azdo_repos"] == "selected, but no Azure projects chosen"
        assert "azdo_repos" in unmet


class TestCodeScopeReportsWhatItDropped:
    """The last return value of ``_resolve_code_scope``.

    A source ticked in setup with nothing behind it used to be stripped silently,
    which is indistinguishable from the source having found nothing.
    """

    def test_a_ticked_source_with_no_repos_is_reported_as_dropped(self):
        config = {"code_scope_configured": True, "code_sources": ["github"], "github_repositories": []}
        sources, owners, github, _projects, _legacy, dropped, _excluded = engine._resolve_code_scope(
            config, None, None, None, None
        )
        assert sources == [] and github == [] and owners == []
        assert dropped == ["github"]

    def test_azure_with_neither_projects_nor_repositories_is_dropped(self):
        config = {
            "code_scope_configured": True,
            "code_sources": ["azure_devops"],
            "azdo_projects": [],
            "azdo_repositories": [],
        }
        sources, _owners, _github, _projects, _legacy, dropped, _excluded = engine._resolve_code_scope(
            config, None, None, None, None
        )
        assert sources == []
        assert dropped == ["azure_devops"]

    def test_a_source_with_a_real_scope_is_not_dropped(self):
        config = {"code_scope_configured": True, "code_sources": ["github"], "github_repositories": ["o/r"]}
        sources, _owners, github, _projects, _legacy, dropped, _excluded = engine._resolve_code_scope(
            config, None, None, None, None
        )
        assert sources == ["github"] and github == ["o/r"]
        assert dropped == []

    def test_an_owner_is_scope_enough_to_keep_github(self):
        """Owners are the GitHub analog of Azure projects — they count as scope."""
        config = {
            "code_scope_configured": True,
            "code_sources": ["github"],
            "github_owners": ["acme"],
            "github_repositories": [],
        }
        sources, owners, github, _projects, _legacy, dropped, _excluded = engine._resolve_code_scope(
            config, None, None, None, None
        )
        assert sources == ["github"] and owners == ["acme"] and github == []
        assert dropped == []

    def test_nothing_is_dropped_before_the_scope_has_ever_been_configured(self, monkeypatch):
        # Defaults are a guess, not a choice — stripping them would report a
        # decision the user never made. An unconfigured GitHub source with an
        # empty scope is "not resolved yet", not "empty": the collector resolves
        # it and reports its own failure if the token can list nothing.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        config = {"code_scope_configured": False, "code_sources": ["github"], "github_repositories": []}
        sources, *_rest, dropped = engine._resolve_code_scope(config, None, None, None, None)
        assert sources == ["github"]
        assert dropped == []


class TestGitHubOwnerScopeResolution:
    """Where the owner list comes from when the caller does not supply one."""

    def test_an_explicit_override_wins_over_saved_owners(self):
        config = {"code_scope_configured": True, "code_sources": ["github"], "github_owners": ["saved"]}
        _sources, owners, *_rest = engine._resolve_code_scope(
            config, None, None, None, None, github_owners=["override"]
        )
        assert owners == ["override"]

    def test_a_bare_token_enables_github_without_resolving_a_scope(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")

        sources, owners, github, *_rest = engine._resolve_code_scope(None, None, None, None, None)

        # The point of the change: a token alone now produces code coverage
        # instead of a report that reads like a quiet day. Which owners that
        # means is the collector's job — resolution stays network-free.
        assert sources == ["github"]
        assert owners == [] and github == []

    def test_scope_resolution_never_touches_the_network(self, monkeypatch):
        """It runs on every path, including inside unit tests.

        Discovering here blocked the standup's critical path on a GitHub call and
        put a live 401 inside the test suite.
        """
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")

        def _boom(*args, **kwargs):
            raise AssertionError("scope resolution must not call GitHub")

        monkeypatch.setattr("yeaboi.tools.github._get_github_client", _boom)
        monkeypatch.setattr("yeaboi.standup.code_scope.discover_github_owners", _boom)

        engine._resolve_code_scope(None, None, None, None, None)

    def test_a_pinned_legacy_repo_is_honoured_verbatim(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "acme/api")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")

        _sources, owners, github, *_rest = engine._resolve_code_scope(None, None, None, None, None)

        # A pin is an explicit narrow scope; it reaches the collector as a
        # repository, which is what stops the auto-discovery branch firing.
        assert owners == [] and github == ["acme/api"]

    def test_no_token_and_no_repo_means_no_github_at_all(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")

        sources, owners, *_rest = engine._resolve_code_scope(None, None, None, None, None)

        assert sources == [] and owners == []


class TestGitHubExcludedRepositoriesResolution:
    """Where the exclusion list comes from — never widens, only trims."""

    def test_an_explicit_override_wins_over_saved_exclusions(self):
        config = {
            "code_scope_configured": True,
            "code_sources": ["github"],
            "github_owners": ["acme"],
            "github_excluded_repositories": ["acme/saved"],
        }
        *_rest, excluded = engine._resolve_code_scope(
            config, None, None, None, None, github_excluded_repositories=["acme/override"]
        )
        assert excluded == ["acme/override"]

    def test_saved_exclusions_apply_when_configured_and_no_override(self):
        config = {
            "code_scope_configured": True,
            "code_sources": ["github"],
            "github_owners": ["acme"],
            "github_excluded_repositories": ["acme/noisy"],
        }
        *_rest, excluded = engine._resolve_code_scope(config, None, None, None, None)
        assert excluded == ["acme/noisy"]

    def test_unconfigured_run_has_no_exclusions(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "token")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")

        *_rest, excluded = engine._resolve_code_scope(None, None, None, None, None)
        assert excluded == []

    def test_empty_override_is_an_explicit_clear(self):
        config = {
            "code_scope_configured": True,
            "code_sources": ["github"],
            "github_owners": ["acme"],
            "github_excluded_repositories": ["acme/noisy"],
        }
        *_rest, excluded = engine._resolve_code_scope(config, None, None, None, None, github_excluded_repositories=[])
        assert excluded == []


class TestSkippedSources:
    def _run(self, monkeypatch, db_path, seeded_session, bundle):
        monkeypatch.setattr(engine.collector, "collect_recent_activity", lambda **kw: bundle)
        monkeypatch.setattr(
            engine.sprint_context,
            "gather",
            lambda state, **kw: __import__("yeaboi.standup.sprint_context", fromlist=["SprintContext"]).SprintContext(
                sprint_name="S", start_date="2026-07-06", sprint_length_weeks=2
            ),
        )
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        monkeypatch.setattr(engine, "_detect_tracker_identity", lambda: ("", []))
        monkeypatch.setattr(engine, "_detect_git_identity", lambda repo: [])
        return engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)

    def test_skipped_sources_land_on_report(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.collector import ActivityBundle

        bundle = ActivityBundle(
            items=[],
            counts=[("jira", 0)],
            skipped=[("github", "STANDUP_GITHUB_REPO not set")],
        )
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        assert report.skipped_sources == (("github", "STANDUP_GITHUB_REPO not set"),)

    def test_partial_coverage_advises_configuring_skipped_sources(self, monkeypatch, db_path, seeded_session):
        # Jira ran but GitHub/AzDO were SELECTED and could not run → the report
        # itself must say so (⚠ Notices), not just the Activity detail.
        from yeaboi.standup.collector import ActivityBundle

        monkeypatch.setattr(
            engine,
            "_skipped_sources",
            lambda *a, **kw: ([], {"github", "azure_devops"}),
        )
        bundle = ActivityBundle(
            items=[],
            counts=[("jira", 2)],
            skipped=[
                ("github", "STANDUP_GITHUB_REPO not set"),
                ("azure_devops", "AZURE_DEVOPS_PROJECT not set"),
            ],
        )
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        notice = next((w for w in report.warnings if w.startswith("Not scanned:")), "")
        assert "GitHub (STANDUP_GITHUB_REPO not set)" in notice
        assert "Azure DevOps tickets (AZURE_DEVOPS_PROJECT not set)" in notice
        assert "connect them in .env" in notice
        assert notice == report.warnings[-1]  # advisory, so auth/LLM problems stay on top

    def test_unselected_source_is_reported_but_never_warned_about(self, monkeypatch, db_path, seeded_session):
        # Deliberately not ticking GitHub is a choice, not a problem. It belongs in
        # the report's "Not scanned" panel — never in a ⚠ notice that would repeat
        # on every run for the rest of the team's life.
        from yeaboi.standup.collector import ActivityBundle

        monkeypatch.setattr(engine, "_skipped_sources", lambda *a, **kw: ([], set()))
        bundle = ActivityBundle(
            items=[],
            counts=[("jira", 2)],
            skipped=[("github", "not selected in setup")],
        )
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        assert report.skipped_sources == (("github", "not selected in setup"),)
        assert not [w for w in report.warnings if w.startswith("Not scanned:")]

    def test_missing_sdk_always_warns_even_if_unselected(self, monkeypatch, db_path, seeded_session):
        # An ImportError is never a choice — the collector only records it for a
        # source it actually tried to run.
        from yeaboi.standup.collector import ActivityBundle

        monkeypatch.setattr(engine, "_skipped_sources", lambda *a, **kw: ([], set()))
        bundle = ActivityBundle(
            items=[],
            counts=[("jira", 2)],
            skipped=[("notion", "SDK not installed")],
        )
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        notice = next((w for w in report.warnings if w.startswith("Not scanned:")), "")
        assert "Notion (SDK not installed)" in notice

    def test_nothing_configured_keeps_single_generic_notice(self, monkeypatch, db_path, seeded_session):
        # All sources skipped → the existing "No activity sources configured"
        # notice already advises; no duplicate per-source line.
        from yeaboi.standup.collector import ActivityBundle

        bundle = ActivityBundle(
            items=[],
            counts=[],
            skipped=[("github", "STANDUP_GITHUB_REPO not set"), ("jira", "JIRA_PROJECT_KEY not set")],
        )
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        assert any(w.startswith("No activity sources configured") for w in report.warnings)
        assert not any(w.startswith("Not scanned:") for w in report.warnings)

    def test_no_skipped_sources_no_notice(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.collector import ActivityBundle

        bundle = ActivityBundle(items=[], counts=[("jira", 2)], skipped=[])
        report = self._run(monkeypatch, db_path, seeded_session, bundle)
        assert not any(w.startswith("Not scanned:") for w in report.warnings)


class TestMemberLinks:
    def test_dedupes_by_url_labels_by_key_and_caps(self):
        acts = [
            {"kind": "update", "title": "moved PSOT-1", "key": "PSOT-1", "url": "https://j/browse/PSOT-1"},
            {"kind": "comment", "title": "commented on PSOT-1", "key": "PSOT-1", "url": "https://j/browse/PSOT-1"},
            {"kind": "commit", "title": "a really long commit message that goes on", "key": "", "url": "https://g/c1"},
            {"kind": "wip", "title": "no url here"},
        ] + [{"kind": "pr", "title": f"pr {i}", "key": f"#{i}", "url": f"https://g/pr/{i}"} for i in range(10)]
        links = engine._member_links(acts)
        assert links[0] == ("PSOT-1", "https://j/browse/PSOT-1")  # deduped: one entry for the ticket
        assert links[1][0] == "a really long commit message that goes on"[:40]  # keyless → truncated title label
        assert len(links) == 6  # capped

    def test_links_land_on_fallback_member_updates(self, monkeypatch, db_path, seeded_session):
        items = [
            {
                "author": "Alice",
                "kind": "update",
                "title": "moved PSOT-9 to Done",
                "source": "jira",
                "key": "PSOT-9",
                "url": "https://x.atlassian.net/browse/PSOT-9",
            },
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        report = engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.links == (("PSOT-9", "https://x.atlassian.net/browse/PSOT-9"),)


class TestMemberEvidence:
    def test_keeps_title_kind_repo_status_and_dedupes_by_url(self):
        acts = [
            {
                "kind": "commit",
                "title": "Fix login redirect",
                "key": "78e4201d",
                "url": "https://g/c1",
                "repository": "yeaboi/web",
                "status": "",
                "timestamp": "2026-07-30T09:15:00",
            },
            {"kind": "commit", "title": "Fix login redirect", "key": "78e4201d", "url": "https://g/c1"},
        ]
        rows = engine._member_evidence(acts)
        assert len(rows) == 1
        row = rows[0]
        assert (row.kind, row.key, row.title) == ("commit", "78e4201d", "Fix login redirect")
        assert (row.repository, row.timestamp) == ("yeaboi/web", "2026-07-30T09:15:00")

    def test_review_sharing_the_pr_url_stays_a_separate_row(self):
        # An AzDO review vote and the member's own PR row both point at the PR
        # URL; GitHub review rows fall back to the PR's html_url. Reviewing and
        # authoring are different work — neither may swallow the other.
        acts = [
            {
                "kind": "pr",
                "title": "Add retry",
                "key": "!7",
                "url": "https://a/pullrequest/7",
                "timestamp": "2026-08-07T16:00:00",
            },
            {
                "kind": "review",
                "title": "approved PR !7: Add retry",
                "key": "review:7:guid-vic",
                "url": "https://a/pullrequest/7",
                "timestamp": "2026-08-07T15:00:00",
            },
        ]
        rows = engine._member_evidence(acts)
        assert [(r.kind, r.key) for r in rows] == [("pr", "!7"), ("review", "review:7:guid-vic")]

    def test_urlless_items_survive_and_dedupe_by_identity(self):
        # Unlike _member_links, an in-progress ticket with no URL still says something.
        acts = [
            {"kind": "wip", "title": "Widen audit windows", "key": "PSOT-1613"},
            {"kind": "wip", "title": "Widen audit windows", "key": "PSOT-1613"},
        ]
        rows = engine._member_evidence(acts)
        assert len(rows) == 1
        assert rows[0].url == ""

    def test_same_pr_merge_from_both_sides_is_one_row(self):
        # A merged PR lands as two merge commits — branch-side and target-side —
        # with different SHAs/URLs but the same subject. One merge, one row.
        acts = [
            {
                "kind": "commit",
                "title": "Merge pull request 48780 from psot/jenkins into master",
                "key": "e8bc280c",
                "url": "https://a/c/e8bc280c",
                "repository": "org/tf-jenkins",
                "timestamp": "2026-08-07T16:29:44",
            },
            {
                "kind": "commit",
                "title": "Merge pull request 48780 from psot/jenkins into master",
                "key": "31a595f1",
                "url": "https://a/c/31a595f1",
                "repository": "org/tf-jenkins",
                "timestamp": "2026-08-07T15:48:17",
            },
        ]
        rows = engine._member_evidence(acts)
        assert len(rows) == 1
        assert rows[0].key == "e8bc280c"  # newest survives

    def test_provenance_tailed_commits_on_one_pr_stay_separate_rows(self):
        # The GitHub collector appends " (PR #91)" to every commit found on a
        # PR branch — those are distinct authored commits, not merges, and must
        # not collapse into one row.
        acts = [
            {"kind": "commit", "title": "Add retry (PR #91)", "key": "aaa1", "url": "https://g/aaa1"},
            {"kind": "commit", "title": "Fix the test (PR #91)", "key": "bbb2", "url": "https://g/bbb2"},
        ]
        assert len(engine._member_evidence(acts)) == 2

    def test_pr_merges_in_different_repos_stay_separate_rows(self):
        acts = [
            {
                "kind": "commit",
                "title": "Merge pull request 12 from x",
                "key": "a",
                "url": "https://a/1",
                "repository": "org/one",
            },
            {
                "kind": "commit",
                "title": "Merge pull request 12 from x",
                "key": "b",
                "url": "https://a/2",
                "repository": "org/two",
            },
        ]
        assert len(engine._member_evidence(acts)) == 2

    def test_caps_at_thirty_preserving_order(self):
        acts = [{"kind": "pr", "title": f"pr {i}", "key": f"#{i}", "url": f"https://g/pr/{i}"} for i in range(35)]
        rows = engine._member_evidence(acts)
        assert len(rows) == 30
        assert rows[0].key == "#0" and rows[29].key == "#29"

    def test_prefers_clean_summary_over_action_title(self):
        # Jira update/comment titles are action phrases ("updated KEY '…'");
        # the collector also sends the raw ticket summary, which the row shows.
        acts = [
            {
                "kind": "update",
                "title": "updated PSOT-1492 'Wiz Part 1 Q3 2026'",
                "summary": "Wiz Part 1 Q3 2026",
                "key": "PSOT-1492",
                "url": "https://j/browse/PSOT-1492",
            }
        ]
        rows = engine._member_evidence(acts)
        assert rows[0].title == "Wiz Part 1 Q3 2026"

    def test_orders_newest_first_with_timestampless_wip_last(self):
        # The day's movement belongs in the visible top rows; carried WIP
        # (which Jira stamps with an empty timestamp) folds.
        acts = [
            {"kind": "wip", "title": "Carried ticket", "key": "PSOT-1", "url": "https://j/1", "timestamp": ""},
            {
                "kind": "update",
                "title": "Old move",
                "key": "PSOT-2",
                "url": "https://j/2",
                "timestamp": "2026-07-30T08:00:00",
            },
            {
                "kind": "update",
                "title": "Fresh move",
                "key": "PSOT-3",
                "url": "https://j/3",
                "timestamp": "2026-07-31T17:00:00",
            },
        ]
        rows = engine._member_evidence(acts)
        assert [r.key for r in rows] == ["PSOT-3", "PSOT-2", "PSOT-1"]

    def test_same_ticket_latest_event_wins_dedupe(self):
        # A Done transition and the issue's own row share a URL; the later
        # event (the issue row, stamped with issue.updated) is the one kept —
        # so a finished ticket shows its clean title and final status.
        acts = [
            {
                "kind": "update",
                "title": "moved PSOT-9 'Fix login' to Done",
                "summary": "Fix login",
                "key": "PSOT-9",
                "url": "https://j/browse/PSOT-9",
                "status": "Done",
                "timestamp": "2026-07-31T15:00:00",
            },
            {
                "kind": "issue",
                "title": "Fix login",
                "key": "PSOT-9",
                "url": "https://j/browse/PSOT-9",
                "status": "Done",
                "timestamp": "2026-07-31T15:00:05",
            },
        ]
        rows = engine._member_evidence(acts)
        assert len(rows) == 1
        assert (rows[0].kind, rows[0].title, rows[0].status) == ("issue", "Fix login", "Done")

    def test_pr_children_become_nested_evidence_newest_first(self):
        acts = [
            {
                "kind": "pr",
                "key": "!91",
                "title": "Widen deploy checkboxes",
                "url": "https://a/pr/91",
                "repository": "acme/infra",
                "status": "merged",
                "timestamp": "2026-07-31T16:00:00",
                "children": [
                    {
                        "kind": "commit",
                        "key": "aaa1",
                        "title": "old",
                        "url": "https://a/c1",
                        "timestamp": "2026-07-31T10:00:00",
                    },
                    {
                        "kind": "commit",
                        "key": "aaa2",
                        "title": "new",
                        "url": "https://a/c2",
                        "timestamp": "2026-07-31T12:00:00",
                    },
                ],
            }
        ]
        rows = engine._member_evidence(acts)
        assert [c.key for c in rows[0].children] == ["aaa2", "aaa1"]
        assert rows[0].children[0].children == ()

    def test_hierarchy_fields_survive_to_evidence_rows(self):
        acts = [
            {
                "kind": "issue",
                "title": "SSO error states",
                "key": "PSOT-3",
                "url": "https://j/browse/PSOT-3",
                "issue_type": "Sub-task",
                "parent_key": "PSOT-1",
                "subtask": True,
            }
        ]
        row = engine._member_evidence(acts)[0]
        assert (row.issue_type, row.parent_key, row.subtask) == ("Sub-task", "PSOT-1", True)
        # Tracker rows ARE tickets — they never name ticket_keys.
        assert row.ticket_keys == ()

    def test_code_rows_name_only_gated_exact_references(self):
        acts = [
            {
                "kind": "pr",
                "title": "PSOT-12 enable SSO",
                "key": "#91",
                "url": "https://g/pr/91",
                "branch": "feature/UTF-8-support",  # ticket-shaped, not a ticket
                "body": "Relates to ab#77.",
                "work_item_ids": ("88",),
            }
        ]
        row = engine._member_evidence(acts, prefixes=frozenset({"PSOT"}), work_item_ids=frozenset({"77"}))[0]
        assert row.ticket_keys == ("PSOT-12", "#77", "#88")

    def test_without_gates_no_keys_are_named(self):
        # The suppress-only default: a caller that passes no gates gets no
        # claims, never ungated ones.
        acts = [{"kind": "pr", "title": "PSOT-12 enable SSO", "key": "#91", "url": "https://g/pr/91"}]
        assert engine._member_evidence(acts)[0].ticket_keys == ()

    def test_fallback_updates_carry_hierarchy_and_attach_keys(self):
        # End to end through _build_fallback_member_updates: the gates are
        # derived from the report's own tracker items, so the PR's reference
        # to the story becomes a named key on its evidence row.
        grouped = {
            "Ada": [
                {
                    "kind": "issue",
                    "source": "jira",
                    "title": "SSO login flow",
                    "key": "PSOT-1",
                    "url": "https://j/browse/PSOT-1",
                    "issue_type": "Story",
                    "subtask": False,
                },
                {
                    "kind": "issue",
                    "source": "jira",
                    "title": "SSO error states",
                    "key": "PSOT-3",
                    "url": "https://j/browse/PSOT-3",
                    "issue_type": "Sub-task",
                    "parent_key": "PSOT-1",
                    "subtask": True,
                },
                {
                    "kind": "pr",
                    "source": "github",
                    "title": "PSOT-1 enable SSO",
                    "key": "#91",
                    "url": "https://g/pr/91",
                },
            ]
        }
        update = engine._build_fallback_member_updates(grouped, {})[0]
        subtask_row = next(r for r in update.ticketing_evidence if r.key == "PSOT-3")
        assert subtask_row.subtask is True and subtask_row.parent_key == "PSOT-1"
        assert update.code_evidence[0].ticket_keys == ("PSOT-1",)

    def test_grouping_carries_timestamp_and_summary(self):
        items = [
            {
                "author": "Alice",
                "kind": "update",
                "title": "moved PSOT-9 'a' to Done",
                "summary": "a",
                "source": "jira",
                "key": "PSOT-9",
                "url": "https://j/browse/PSOT-9",
                "repository": "",
                "timestamp": "2026-07-30T09:15:00",
            }
        ]
        grouped = engine._group_activity_by_author(items, ["Alice"])
        assert grouped["Alice"][0]["timestamp"] == "2026-07-30T09:15:00"
        assert grouped["Alice"][0]["summary"] == "a"

    def test_evidence_lands_on_fallback_member_updates(self):
        grouped = {
            "Alice": [
                {
                    "kind": "commit",
                    "title": "Fix login",
                    "key": "78e4201d",
                    "url": "https://g/c1",
                    "repository": "yeaboi/web",
                },
                {
                    "kind": "update",
                    "title": "moved PSOT-9",
                    "key": "PSOT-9",
                    "url": "https://j/browse/PSOT-9",
                    "source": "jira",
                },
            ],
        }
        updates = engine._build_fallback_member_updates(grouped, {})
        alice = updates[0]
        assert [e.key for e in alice.code_evidence] == ["78e4201d"]
        assert [e.key for e in alice.ticketing_evidence] == ["PSOT-9"]
        assert alice.documentation_evidence == ()


def _pr_act(**over):
    base = {
        "kind": "pr",
        "key": "!91",
        "pr_id": 91,
        "branch": "feat/login",
        "title": "Enable SSO",
        "url": "https://a/pr/91",
        "repository": "acme/web",
        "status": "merged",
        "timestamp": "2026-07-31T16:00:00",
    }
    base.update(over)
    return base


def _commit_act(title, **over):
    base = {
        "kind": "commit",
        "key": "aaaa0001",
        "title": title,
        "url": "https://a/c/1",
        "repository": "acme/web",
        "timestamp": "2026-07-31T14:00:00",
    }
    base.update(over)
    return base


class TestNestPrCommits:
    def test_merge_number_folds_commit_under_pr(self):
        acts = [_pr_act(), _commit_act("Merge pull request 91 from feat/login (web)")]
        nested = engine._nest_pr_commits(acts)
        assert [a["kind"] for a in nested] == ["pr"]
        assert [c["title"] for c in nested[0]["children"]] == ["Merge pull request 91 from feat/login (web)"]

    def test_github_pr_suffix_matches(self):
        # The github PR-branch scan appends "(PR #N)" to each commit subject.
        acts = [_pr_act(key="#91"), _commit_act("Fix redirect loop (PR #91)")]
        nested = engine._nest_pr_commits(acts)
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_github_squash_merge_suffix_matches(self):
        # Squash-merged default-branch commits end in "(#N)", no "PR" word.
        acts = [_pr_act(key="#91"), _commit_act("Fix redirect loop (#91)")]
        nested = engine._nest_pr_commits(acts)
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_azdo_squash_merge_subject_matches(self):
        # AzDO squash merges read "Merged PR 123: Title (repo)".
        acts = [_pr_act(key="!91"), _commit_act("Merged PR 91: Fix redirect loop (web)")]
        nested = engine._nest_pr_commits(acts)
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_branch_fallback_when_number_is_another_pr(self):
        # The merge subject names a PR that is not in the window; the source
        # branch still identifies the one that is.
        acts = [_pr_act(pr_id=91, branch="feat/login"), _commit_act("Merge pull request 90 from feat/login")]
        nested = engine._nest_pr_commits(acts)
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_branch_fallback_strips_the_github_owner_prefix(self):
        # Real GitHub merge subjects say "from <owner>/<branch>" while the PR
        # item's branch is the bare head ref.
        acts = [_pr_act(pr_id=91, branch="feat/login"), _commit_act("Merge pull request 90 from octo/feat/login")]
        nested = engine._nest_pr_commits(acts)
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_wrong_repo_stays_top_level(self):
        acts = [_pr_act(), _commit_act("Merge pull request 91 from feat/login", repository="acme/other")]
        nested = engine._nest_pr_commits(acts)
        assert [a["kind"] for a in nested] == ["pr", "commit"]
        assert nested[0]["children"] == []

    def test_plain_commit_and_non_code_kinds_stay_put(self):
        review = {"kind": "review", "key": "r1", "title": "approved", "repository": "acme/web"}
        acts = [_pr_act(), _commit_act("Hotfix the flaky retry"), review]
        nested = engine._nest_pr_commits(acts)
        assert [a["kind"] for a in nested] == ["pr", "commit", "review"]

    def test_no_prs_returns_acts_unchanged(self):
        acts = [_commit_act("Merge pull request 91 from feat/login")]
        assert engine._nest_pr_commits(acts) is acts

    def test_caller_items_are_not_mutated(self):
        # The same acts also feed _member_links and the counts — the PR dict is
        # copied, and the input list keeps every item.
        pr = _pr_act()
        commit = _commit_act("Merge pull request 91 from feat/login")
        acts = [pr, commit]
        engine._nest_pr_commits(acts)
        assert "children" not in pr
        assert acts == [pr, commit]


class TestActivityCount:
    def test_fallback_updates_count_attributed_items(self):
        grouped = {
            "Alice": [{"kind": "update", "title": "a"}, {"kind": "commit", "title": "b"}],
            "Quentin": [],
        }
        updates = engine._build_fallback_member_updates(grouped, {})
        by_name = {u.name: u for u in updates}
        assert by_name["Alice"].activity_count == 2
        assert by_name["Quentin"].activity_count == 0

    def test_llm_path_sets_activity_count(self, monkeypatch, db_path, seeded_session):
        items = [
            {"author": "Alice", "kind": "update", "title": "moved PSOT-9", "source": "jira", "key": "PSOT-9"},
            {"author": "Alice", "kind": "comment", "title": "commented on PSOT-9", "source": "jira", "key": "PSOT-9"},
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 2)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        report = engine.run_standup(seeded_session, db_path=db_path, dry_run=True, deliver=False)
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.activity_count == 2


class TestDayOverDay:
    """Yesterday/today/tomorrow analysis, blocker signals, and confidence trend wiring."""

    @staticmethod
    def _prompt_text(captured: dict) -> str:
        """Flatten the captured invoke() payload (str or [HumanMessage]) to text."""
        m = captured["prompt"]
        if isinstance(m, str):
            return m
        parts = []
        for msg in m:
            content = getattr(msg, "content", "")
            parts.append(content if isinstance(content, str) else json.dumps(content))
        return "\n".join(parts)

    def _seed_yesterday(self, db_path, **member_kwargs):
        from yeaboi.agent.state import MemberUpdate, StandupReport

        member = MemberUpdate(name="Alice", summary="Started PSOT-9 auth work", **member_kwargs)
        report = StandupReport(date="2026-07-09", session_id="sess-1", member_updates=(member,))
        with StandupStore(db_path) as store:
            store.record_run(report, status="success")

    def test_llm_progress_note_and_outlook_land(self, monkeypatch, db_path, seeded_session):
        self._seed_yesterday(db_path)
        items = [{"author": "Alice", "kind": "commit", "title": "auth polish", "source": "github"}]
        _patch_common(monkeypatch, items=items, counts=[("github", 1)])
        llm_json = json.dumps(
            {
                "members": [
                    {
                        "name": "Alice",
                        "summary": "Polished auth",
                        "progress_note": "Continued yesterday's PSOT-9 auth work.",
                        "outlook": "Likely to open the auth PR.",
                    },
                    {"name": "Bob", "summary": "quiet", "progress_note": "Invented comparison.", "outlook": ""},
                ],
                "team_summary": "ok",
            }
        )
        captured: dict = {}

        def _fake_invoke(self, m):
            captured["prompt"] = m
            return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": _fake_invoke})())

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        names = {m.name: m for m in report.member_updates}
        assert names["Alice"].progress_note == "Continued yesterday's PSOT-9 auth work."
        assert names["Alice"].outlook == "Likely to open the auth PR."
        # Bob has no entry in yesterday's report → the model cannot invent one.
        assert names["Bob"].progress_note == ""
        # The prompt carried yesterday's context for Alice.
        prompt_text = self._prompt_text(captured)
        assert "Started PSOT-9 auth work" in prompt_text
        assert '"yesterday"' in prompt_text

    def test_no_previous_report_clears_progress_note(self, monkeypatch, db_path, seeded_session):
        items = [{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}]
        _patch_common(monkeypatch, items=items, counts=[("github", 1)])
        llm_json = json.dumps(
            {
                "members": [{"name": "Alice", "summary": "s", "progress_note": "Made-up yesterday."}],
                "team_summary": "ok",
            }
        )
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.progress_note == ""

    def test_blocker_signals_fold_in_when_llm_omits(self, monkeypatch, db_path, seeded_session):
        items = [
            {
                "author": "Alice",
                "kind": "issue",
                "key": "PSOT-9",
                "title": "Auth",
                "status": "Blocked",
                "source": "jira",
            }
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 1)])
        captured: dict = {}
        llm_json = json.dumps({"members": [{"name": "Alice", "summary": "s", "blockers": ""}], "team_summary": "ok"})

        def _fake_invoke(self, m):
            captured["prompt"] = m
            return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": _fake_invoke})())
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        # The deterministic signal survives even though the model dropped it.
        assert alice.blockers == "PSOT-9 'Auth' is in Blocked"
        assert "PSOT-9 'Auth' is in Blocked" in self._prompt_text(captured)

    def test_fallback_sets_blockers_outlook_and_progress_note(self, monkeypatch, db_path, seeded_session):
        self._seed_yesterday(db_path)
        items = [
            {
                "author": "Alice",
                "kind": "issue",
                "key": "PSOT-9",
                "title": "Auth",
                "status": "Blocked",
                "source": "jira",
            },
            {
                "author": "Alice",
                "kind": "wip",
                "key": "PSOT-14",
                "title": "Session store",
                "status": "In Progress",
                "source": "jira",
            },
        ]
        _patch_common(monkeypatch, items=items, counts=[("jira", 2)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key set"))

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.blockers == "PSOT-9 'Auth' is in Blocked"
        assert alice.outlook == "Likely continuing: Session store."
        # Yesterday's summary mentioned PSOT-9 and it's active again today.
        assert alice.progress_note == "Still on PSOT-9 (carried over from the last standup)."

    def test_confidence_trend_from_seeded_history(self, monkeypatch, db_path, seeded_session):
        from yeaboi.agent.state import StandupReport

        with StandupStore(db_path) as store:
            store.record_run(
                StandupReport(date="2026-07-09", session_id="sess-1", confidence_pct=80, confidence_label="At risk"),
                status="success",
            )
        items = [{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}]
        _patch_common(monkeypatch, items=items, counts=[("github", 1)])
        llm_json = json.dumps({"members": [], "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        # Base burn-down pct is 100 (10/20 points on day 5 of 10); yesterday was 80.
        assert report.confidence_pct == 100
        assert report.confidence_trend == "improving"
        assert report.confidence_delta == 20
        assert "Up 20 pts since the last standup." in report.confidence_rationale

    def test_same_day_rerun_is_not_yesterday(self, monkeypatch, db_path, seeded_session):
        from yeaboi.agent.state import MemberUpdate, StandupReport

        # An earlier run TODAY must not become the comparison baseline.
        with StandupStore(db_path) as store:
            store.record_run(
                StandupReport(
                    date="2026-07-10",
                    session_id="sess-1",
                    confidence_pct=55,
                    member_updates=(MemberUpdate(name="Alice", summary="Earlier rerun today"),),
                ),
                status="success",
            )
        items = [{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}]
        _patch_common(monkeypatch, items=items, counts=[("github", 1)])
        captured: dict = {}
        llm_json = json.dumps(
            {"members": [{"name": "Alice", "summary": "s", "progress_note": "vs earlier today"}], "team_summary": "ok"}
        )

        def _fake_invoke(self, m):
            captured["prompt"] = m
            return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": _fake_invoke})())
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        alice = next(m for m in report.member_updates if m.name == "Alice")
        assert alice.progress_note == ""  # no true yesterday → clamp wins
        assert "Earlier rerun today" not in self._prompt_text(captured)
        assert report.confidence_trend == ""  # same-day pct filtered from the trend


class TestTranscriptReviewSweep:
    """The automatic pre-standup transcript sweep wired into run_standup."""

    def _patch_llm(self, monkeypatch, captured: dict):
        llm_json = json.dumps(
            {"members": [{"name": "Alice", "summary": "s", "progress_note": "p"}], "team_summary": "ok"}
        )

        def _fake_invoke(self, m):
            captured["prompt"] = m
            return _FakeResp(llm_json)

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": _fake_invoke})())

    def _prompt_text(self, captured: dict) -> str:
        return str(captured.get("prompt", ""))

    def test_disabled_by_kwarg_skips_the_sweep(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])
        self._patch_llm(monkeypatch, {})
        called = []
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda *a, **k: called.append(1) or [],
        )
        engine.run_standup(
            seeded_session,
            deliver=False,
            db_path=db_path,
            today=date(2026, 7, 10),
            review_transcripts=False,
        )
        assert called == []

    def test_disabled_by_config_skips_the_sweep(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.save_config(
                seeded_session,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                transcript_review_enabled=False,
            )
        _patch_common(monkeypatch, items=[], counts=[])
        self._patch_llm(monkeypatch, {})
        called = []
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda *a, **k: called.append(1) or [],
        )
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert called == []

    def test_a_raising_sweep_is_never_fatal(self, monkeypatch, db_path, seeded_session):
        """This sits on the standup critical path — it must not break a standup."""
        _patch_common(monkeypatch, items=[], counts=[])
        self._patch_llm(monkeypatch, {})

        def _boom(*a, **k):
            raise RuntimeError("transcripts exploded")

        monkeypatch.setattr("yeaboi.standup.transcript_review.sweep_and_review", _boom)
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert report.date == "2026-07-10"
        assert any("Transcript review skipped" in w for w in report.warnings)

    def test_corrections_reach_the_summary_prompt(self, monkeypatch, db_path, seeded_session):
        from yeaboi.agent.state import StandupReport as _Report
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.record_run(
                _Report(
                    date="2026-07-09",
                    session_id=seeded_session,
                    member_updates=(MemberUpdate(name="Alice", summary="Did the login work"),),
                )
            )
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "x", "source": "github"}],
            counts=[("github", 1)],
        )
        captured: dict = {}
        self._patch_llm(monkeypatch, captured)
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda *a, **k: [
                TranscriptReview(
                    standup_date="2026-07-09",
                    claims=(TranscriptClaim(member="Alice", claim="also shipped the alerting PR", status="missing"),),
                )
            ],
        )
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert "also shipped the alerting PR" in self._prompt_text(captured)

    def test_findings_become_notices_capped(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[("github", 1)])
        self._patch_llm(monkeypatch, {})
        gaps = tuple(StandupGap(title=f"Gap number {i}", scope="product") for i in range(6))
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda *a, **k: [TranscriptReview(standup_date="2026-07-09", gaps=gaps)],
        )
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        notices = [w for w in report.warnings if "Gap number" in w]
        assert len(notices) == engine._MAX_TRANSCRIPT_NOTICES
        assert any("and 3 more transcript-review" in w for w in report.warnings)

    def test_sweep_runs_before_activity_collection(self, monkeypatch, db_path, seeded_session):
        """Corrections must be in hand BEFORE today's summaries are written."""
        order: list[str] = []
        _patch_common(monkeypatch, items=[], counts=[])
        self._patch_llm(monkeypatch, {})
        original = engine.collector.collect_recent_activity
        monkeypatch.setattr(
            engine.collector,
            "collect_recent_activity",
            lambda **kw: order.append("collect") or original(**kw),
        )
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda *a, **k: order.append("review") or [],
        )
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert order[:2] == ["review", "collect"]


class TestNudgeInTheReport:
    """The nudge rides on report.warnings — a BROADCAST surface (Slack, email,
    exports), which is why a single miss stays out of it."""

    def _seed_misses(self, db_path, session, days, *, status="success"):
        from yeaboi.agent.state import StandupReport
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            for day in days:
                store.record_run(StandupReport(session_id=session, date=day), status=status)

    def _run(self, monkeypatch, db_path, seeded_session, today=date(2026, 7, 10)):
        _patch_common(monkeypatch, items=[], counts=[])
        monkeypatch.setattr("yeaboi.standup.transcript_review.sweep_and_review", lambda *a, **k: [])
        return engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=today)

    def test_a_persistent_miss_reaches_the_report(self, monkeypatch, db_path, seeded_session):
        self._seed_misses(db_path, seeded_session, [f"2026-07-0{d}" for d in range(1, 8)])
        report = self._run(monkeypatch, db_path, seeded_session)
        assert any("never checked against their meetings" in w or "gone unchecked" in w for w in report.warnings)

    def test_a_single_miss_stays_out_of_the_broadcast(self, monkeypatch, db_path, seeded_session):
        """ "You forgot a file" does not belong in a team Slack channel."""
        self._seed_misses(db_path, seeded_session, ["2026-07-07", "2026-07-08", "2026-07-09"])
        report = self._run(monkeypatch, db_path, seeded_session)
        assert not any("transcript" in w.lower() and "unchecked" in w.lower() for w in report.warnings)

    def test_the_nudge_survives_the_notice_cap(self, monkeypatch, db_path, seeded_session):
        """A day with three findings must not truncate away the reason a fourth
        standup was never checked at all."""
        from yeaboi.agent.state import TranscriptReview

        self._seed_misses(db_path, seeded_session, [f"2026-07-0{d}" for d in range(1, 8)])
        noisy = [TranscriptReview(warnings=tuple(f"finding {i}" for i in range(8)))]
        monkeypatch.setattr("yeaboi.standup.transcript_review.sweep_and_review", lambda *a, **k: noisy)
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.carry_forward", lambda r, p: ({}, list(noisy[0].warnings))
        )
        _patch_common(monkeypatch, items=[], counts=[])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("gone unchecked" in w or "never checked" in w for w in report.warnings)

    def test_the_opt_out_silences_it(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.store import StandupStore

        self._seed_misses(db_path, seeded_session, [f"2026-07-0{d}" for d in range(1, 8)])
        with StandupStore(db_path) as store:
            store.save_config(
                seeded_session,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                transcript_review_enabled=False,
            )
        report = self._run(monkeypatch, db_path, seeded_session)
        assert not any("unchecked" in w for w in report.warnings)

    def test_a_broken_nudge_never_breaks_a_standup(self, monkeypatch, db_path, seeded_session):
        def _boom(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr("yeaboi.standup.transcripts.transcript_nudge", _boom)
        report = self._run(monkeypatch, db_path, seeded_session)
        assert report is not None


class TestTranscriptNudgeEntryPoint:
    def test_returns_a_falsy_nudge_when_there_is_nothing_to_say(self, db_path, seeded_session):
        assert not engine.transcript_nudge(seeded_session, db_path=db_path, today=date(2026, 7, 10))

    def test_reads_config_so_the_opt_out_applies(self, monkeypatch, db_path, seeded_session):
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.transcripts.transcript_nudge",
            lambda sid, **kw: seen.update(kw) or None,
        )
        engine.transcript_nudge(seeded_session, db_path=db_path, today=date(2026, 7, 10))
        assert "config" in seen


class TestImportTranscript:
    """Text that never was a file — a paste, a pipe, an agent argument."""

    @pytest.fixture
    def managed(self, tmp_path, monkeypatch):
        d = tmp_path / "transcripts"
        d.mkdir()
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", d)
        return d

    def test_returns_a_source_describing_what_landed(self, managed):
        source = engine.import_transcript(
            "Alice: shipped auth\nBob: reviewed it\nCara: blocked", today=date(2026, 7, 10)
        )
        assert source.covered_date == "2026-07-10"
        assert source.speakers == ("Alice", "Bob", "Cara")
        # The part worth surfacing: it narrows what a review may conclude.
        assert source.attribution == "labelled"

    def test_writes_into_the_managed_folder(self, managed):
        source = engine.import_transcript("Alice: hi", today=date(2026, 7, 10))
        assert list(managed.iterdir()) == [managed / source.filename]

    def test_explicit_date_lands_in_the_filename(self, managed):
        source = engine.import_transcript("Alice: hi", covered_date="2026-07-08", today=date(2026, 7, 10))
        assert source.filename.startswith("2026-07-08-")
        assert source.covered_date == "2026-07-08"

    def test_empty_text_raises(self, managed):
        with pytest.raises(ValueError, match="empty"):
            engine.import_transcript("   ", today=date(2026, 7, 10))


class TestReviewFromText:
    @pytest.fixture
    def managed(self, tmp_path, monkeypatch):
        d = tmp_path / "transcripts"
        d.mkdir()
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", d)
        return d

    def test_pasted_text_is_imported_and_reviewed(self, monkeypatch, managed, db_path, seeded_session):
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda sid, **kw: seen.update(kw) or [],
        )
        engine.run_transcript_review(
            seeded_session,
            transcript_text="Alice: shipped auth\nBob: reviewed it",
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        # It reaches the sweep as an explicit PATH — an import the user just made
        # is reviewed now, not queued behind a folder backlog.
        assert len(seen["transcript_paths"]) == 1
        assert seen["transcript_paths"][0].endswith(".txt")
        assert (managed / "2026-07-10-pasted.txt").exists()

    def test_standup_date_attributes_the_paste(self, monkeypatch, managed, db_path, seeded_session):
        monkeypatch.setattr("yeaboi.standup.transcript_review.sweep_and_review", lambda sid, **kw: [])
        engine.run_transcript_review(
            seeded_session,
            transcript_text="Alice: hi",
            standup_date="2026-07-08",
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        assert (managed / "2026-07-08-pasted.txt").exists()

    def test_paste_is_prepended_to_explicit_paths(self, monkeypatch, managed, db_path, seeded_session, tmp_path):
        other = tmp_path / "other.vtt"
        other.write_text("WEBVTT")
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.transcript_review.sweep_and_review",
            lambda sid, **kw: seen.update(kw) or [],
        )
        engine.run_transcript_review(
            seeded_session,
            transcript_paths=[str(other)],
            transcript_text="Alice: hi",
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        assert len(seen["transcript_paths"]) == 2
        assert seen["transcript_paths"][1] == str(other)

    def test_a_rejected_paste_comes_back_as_a_warning_not_an_exception(self, managed, db_path, seeded_session):
        """This surface never raises — a bad paste reads like any other reason
        the review found nothing to say. Reachable as `--date 'last tuesday'`."""
        review = engine.run_transcript_review(
            seeded_session,
            transcript_text="Alice: hi",
            standup_date="last tuesday",
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        assert review.gaps == ()
        assert any("Invalid covered_date" in w for w in review.warnings)
        assert list(managed.iterdir()) == []

    def test_oversized_paste_is_a_warning_too(self, monkeypatch, managed, db_path, seeded_session):
        monkeypatch.setattr("yeaboi.standup.transcripts._MAX_BYTES", 20)
        review = engine.run_transcript_review(
            seeded_session,
            transcript_text="Alice: " + "x" * 500,
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        assert any("larger than" in w for w in review.warnings)

    @pytest.mark.parametrize("blank", ["", "    \n   "])
    def test_blank_text_does_not_trigger_an_import(self, blank, managed, db_path, seeded_session):
        review = engine.run_transcript_review(
            seeded_session, transcript_text=blank, db_path=db_path, today=date(2026, 7, 10)
        )
        assert list(managed.iterdir()) == []
        assert any("No unreviewed transcripts" in w for w in review.warnings)

    def test_import_progress_is_reported(self, monkeypatch, managed, db_path, seeded_session):
        monkeypatch.setattr("yeaboi.standup.transcript_review.sweep_and_review", lambda sid, **kw: [])
        phases: list[str] = []
        engine.run_transcript_review(
            seeded_session,
            transcript_text="Alice: hi",
            db_path=db_path,
            today=date(2026, 7, 10),
            on_progress=phases.append,
        )
        assert "Saving the pasted transcript" in phases


class TestTranscriptEntryPoints:
    def test_review_with_no_transcripts_says_so(self, monkeypatch, db_path, seeded_session, tmp_path):
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", tmp_path / "transcripts")
        review = engine.run_transcript_review(seeded_session, db_path=db_path, today=date(2026, 7, 10))
        assert review.gaps == ()
        assert any("No unreviewed transcripts" in w for w in review.warnings)

    def test_review_has_no_file_issues_parameter(self):
        """Structural guarantee: the drafting entry point cannot publish."""
        import inspect

        assert "file_issues" not in inspect.signature(engine.run_transcript_review).parameters

    def test_review_reports_progress(self, monkeypatch, db_path, seeded_session, tmp_path):
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", tmp_path / "transcripts")
        phases: list[str] = []
        engine.run_transcript_review(
            seeded_session, db_path=db_path, today=date(2026, 7, 10), on_progress=phases.append
        )
        assert "Reading transcripts" in phases

    def test_progress_callback_failure_is_swallowed(self, monkeypatch, db_path, seeded_session, tmp_path):
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", tmp_path / "transcripts")

        def _boom(phase):
            raise RuntimeError("ui died")

        review = engine.run_transcript_review(
            seeded_session, db_path=db_path, today=date(2026, 7, 10), on_progress=_boom
        )
        assert review is not None

    def test_filing_without_a_review_is_reported_not_raised(self, db_path):
        result = engine.file_transcript_issues(0, session_id="nope", db_path=db_path)
        assert result.filed == 0
        assert any("No transcript review found" in w for w in result.warnings)

    def test_filing_delegates_to_gap_issues(self, monkeypatch, db_path, seeded_session):
        from yeaboi.agent.state import IssueFilingResult
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            review_id = store.record_review(
                TranscriptReview(session_id=seeded_session, gaps=(StandupGap(fingerprint="fp1"),))
            )
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.gap_issues.file_review_gaps",
            lambda review, **kw: seen.update(review=review, kw=kw) or IssueFilingResult(filed=1),
        )
        result = engine.file_transcript_issues(review_id, db_path=db_path)
        assert result.filed == 1
        assert seen["review"].review_id == review_id


class TestLlmPayloadKeys:
    """`engine._for_llm` is a blacklist, so a new grouped key reaches the prompt by default.

    This pins the grouped key set instead of re-listing the blacklist: adding a
    field to `_group_activity_by_author` fails here, and whoever adds it has to
    decide out loud whether the model should see it. Without this guard, adding
    `body` would silently put a full PR description in every member payload.
    """

    # Split by intent. Anything not in one of these two sets is undecided.
    FOR_THE_MODEL = frozenset({"kind", "title", "summary", "status", "source", "repository"})
    RENDERING_AND_RULES_ONLY = frozenset(
        {
            "key",
            "url",
            "timestamp",
            "pr_id",
            "branch",
            "body",
            "changed_paths",
            "work_item_ids",
            "work_items_known",
            # Story/subtask hierarchy: deterministic, drawn by the web page —
            # the model restating structure the UI renders would be noise.
            "issue_type",
            "parent_key",
            "subtask",
        }
    )

    def _row(self) -> dict:
        item = {
            "author": "Alice",
            "kind": "pr",
            "title": "Add retry",
            "status": "merged",
            "source": "github",
            "key": "#91",
            "url": "https://x/pull/91",
            "repository": "acme/web",
            "timestamp": "2026-07-13T09:00:00",
            "pr_id": "91",
            "branch": "feature/retry",
            "body": "A long pull request description that must never reach the prompt.",
            "changed_files": [f"src/m{i}.py" for i in range(100)],
            "work_item_ids": ["1234"],
            "work_items_known": True,
            "issue_type": "Story",
            "parent_key": "PROJ-1",
            "subtask": False,
        }
        return engine._group_activity_by_author([item], ["Alice"])["Alice"][0]

    def test_every_grouped_key_is_classified(self):
        undecided = set(self._row()) - self.FOR_THE_MODEL - self.RENDERING_AND_RULES_ONLY
        assert not undecided, (
            f"new grouped key(s) {sorted(undecided)} — decide whether the model should see them, "
            "then add them here and (if not) to engine._for_llm's rendering_only tuple"
        )

    def test_the_habit_fields_survive_the_grouping(self):
        row = self._row()
        assert row["body"].startswith("A long pull request description")
        assert len(row["changed_paths"]) == 100
        assert row["work_item_ids"] == ("1234",)

    def test_changed_files_is_renamed_not_carried(self):
        # categories.split_activity reads these dicts; the canonical key would
        # silently reclassify docs-only repository events out of Code.
        assert "changed_files" not in self._row()


class TestPracticesReachBothPaths:
    def _grouped(self) -> dict:
        return {
            "Alice": [
                {
                    "kind": "pr",
                    "key": "#91",
                    "title": "Add retry",
                    "branch": "feature/retry",
                    "body": "",
                    "status": "merged",
                    "source": "github",
                    "repository": "acme/web",
                    "url": "https://x/pull/91",
                    "work_items_known": True,
                }
            ]
        }

    def test_fallback_path_sets_practices(self):
        from yeaboi.standup import habits

        practices = habits.detect_practices(self._grouped())
        updates = engine._build_fallback_member_updates(self._grouped(), {}, practices=practices)
        assert updates[0].practices
        assert updates[0].practices[0].rule == habits.RULE_UNTRACKED_WORK

    def test_fallback_path_without_practices_is_empty_not_none(self):
        updates = engine._build_fallback_member_updates(self._grouped(), {})
        assert updates[0].practices == ()


class TestPracticeFeedbackReachesTheRun:
    """``run_standup`` reads the ledger itself rather than taking it as a parameter.

    That is why it grew no new argument — every surface that runs a standup gets
    the team's corrections without having to remember to pass them, and the
    param-parity check stays green. What matters here is the wiring: both halves
    of the ledger reach ``detect_practices``, and both are shaped by what the
    store actually holds. Whether a given signal then fires is
    ``test_standup_habits.py``'s job.
    """

    def _spy(self, monkeypatch) -> dict:
        seen: dict = {}
        real = engine.habits.detect_practices

        def spy(grouped, **kw):
            seen.update(kw)
            return real(grouped, **kw)

        monkeypatch.setattr(engine.habits, "detect_practices", spy)
        return seen

    def _run(self, monkeypatch, db_path, session):
        _patch_common(
            monkeypatch,
            items=[{"author": "Alice", "kind": "commit", "title": "login page", "source": "github"}],
            counts=[("github", 1)],
        )
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        seen = self._spy(monkeypatch)
        engine.run_standup(session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        return seen

    def test_an_empty_ledger_still_supplies_the_seam(self, monkeypatch, db_path, seeded_session):
        seen = self._run(monkeypatch, db_path, seeded_session)
        assert callable(seen["feedback"])
        assert seen["feedback"]("untracked-work", "url:https://x/pull/91") is False

    def test_a_recorded_verdict_reaches_detection(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.record_practice_feedback(
                seeded_session,
                rule="untracked-work",
                handle="url:https://x/pull/91",
                verdict="down",
                note="that PR is the spike ticket",
                subject="#91",
            )
        seen = self._run(monkeypatch, db_path, seeded_session)
        assert seen["feedback"]("untracked-work", "url:https://x/pull/91") is True
        # Scoped to its rule — the same change may still be an oversized one.
        assert seen["feedback"]("large-change", "url:https://x/pull/91") is False

    def test_the_reason_reaches_the_matching_pass(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.store import StandupStore

        captured: list = []
        monkeypatch.setattr(
            engine.adjudicate, "build_adjudicator", lambda config, corrections=(): captured.append(corrections)
        )
        with StandupStore(db_path) as store:
            store.record_practice_feedback(
                seeded_session,
                rule="untracked-work",
                handle="h1",
                verdict="down",
                note="that PR is the spike ticket",
                subject="#91",
            )
        self._run(monkeypatch, db_path, seeded_session)
        assert captured and captured[0][0]["note"] == "that PR is the spike ticket"

    def test_a_verdict_from_another_session_does_not_leak_in(self, monkeypatch, db_path, seeded_session):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.record_practice_feedback(
                "someone-elses-session", rule="untracked-work", handle="url:https://x/pull/91", verdict="down"
            )
        seen = self._run(monkeypatch, db_path, seeded_session)
        assert seen["feedback"]("untracked-work", "url:https://x/pull/91") is False


class TestAggregateDispatchProtocol:
    """run_standup's use of the aggregate seam: two-pass adjudication."""

    def _canned_llm(self, monkeypatch):
        llm_json = json.dumps({"members": [], "team_summary": "ok"})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: type("R", (), {"content": llm_json})()})(),
        )

    def test_two_pass_feeds_dropped_case_ids_back(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[("jira", 0)])
        self._canned_llm(monkeypatch)
        from yeaboi.standup import adjudicate, aggregate
        from yeaboi.standup.habits import AdjudicationCase

        real = aggregate.aggregate_standup
        calls: list[dict] = []

        def fake(inputs):
            calls.append(inputs)
            result = real(inputs)
            if "dropped_case_ids" not in inputs:
                result["adjudication_cases"] = [
                    {"case_id": "work-0", "subject": "s", "branch": "", "paths": [], "candidates": [["K-1", "t", "x"]]}
                ]
            return result

        monkeypatch.setattr(aggregate, "aggregate_standup", fake)
        seen_cases: list = []

        def fake_adjudicator(cases):
            seen_cases.extend(cases)
            return ["work-0", "bogus-9"]

        monkeypatch.setattr(adjudicate, "build_adjudicator", lambda config, corrections: fake_adjudicator)
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert len(calls) == 2
        # The engine passes every id back sorted; habits' pass-2 intersection
        # is what discards the junk one.
        assert calls[1]["dropped_case_ids"] == ["bogus-9", "work-0"]
        assert isinstance(seen_cases[0], AdjudicationCase)
        assert seen_cases[0].case_id == "work-0"

    def test_failing_adjudicator_keeps_pass_one_result(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[("jira", 0)])
        self._canned_llm(monkeypatch)
        from yeaboi.standup import adjudicate, aggregate

        real = aggregate.aggregate_standup
        calls: list[dict] = []

        def fake(inputs):
            calls.append(inputs)
            result = real(inputs)
            if "dropped_case_ids" not in inputs:
                result["adjudication_cases"] = [
                    {"case_id": "work-0", "subject": "s", "branch": "", "paths": [], "candidates": [["K-1", "t", "x"]]}
                ]
            return result

        monkeypatch.setattr(aggregate, "aggregate_standup", fake)

        def boom(cases):
            raise RuntimeError("adjudicator exploded")

        monkeypatch.setattr(adjudicate, "build_adjudicator", lambda config, corrections: boom)
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert len(calls) == 1  # no second pass — deterministic verdicts stand
        assert report.date == "2026-07-10"


class TestConflictAndProvenanceWiring:
    ITEMS = [
        {
            "author": "Alice",
            "kind": "issue",
            "key": "YEA-12",
            "status": "Done",
            "title": "auth epic",
            "source": "jira",
            "url": "https://j/12",
        },
        {
            "author": "Alice",
            "kind": "pr",
            "status": "open",
            "title": "YEA-12 auth fix",
            "source": "github",
            "url": "https://g/41",
        },
    ]

    def test_conflict_cards_and_audit_trail_land(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("github", 1), ("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))

        assert len(report.conflicts) == 1
        card = report.conflicts[0]
        assert card.entity_id == "YEA-12"
        assert {claim[0] for claim in card.claims} == {"jira", "github"}

        from yeaboi.provenance import ProvenanceChain

        with ProvenanceChain(db_path) as chain:
            assert chain.verify().valid is True
            conflict = chain.get("standup:2026-07-10:conflict:YEA-12:status:status_conflict")
            assert conflict is not None
            assert conflict.inputs == ("https://j/12", "https://g/41")
            assert chain.get("standup:2026-07-10:confidence") is not None

    def test_dry_run_records_no_audit_trail(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("github", 1), ("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))

        report = engine.run_standup(
            seeded_session, deliver=False, dry_run=True, db_path=db_path, today=date(2026, 7, 10)
        )

        # The cards still render — only the side-effecting chain write is skipped.
        assert len(report.conflicts) == 1
        from yeaboi.provenance import ProvenanceChain

        with ProvenanceChain(db_path) as chain:
            assert chain.total() == 0

    def test_failed_audit_write_warns_but_never_fails_the_run(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("github", 1), ("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))

        from yeaboi.standup import provenance_log

        def _boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(provenance_log, "record_run", _boom)
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("Audit trail not recorded" in w for w in report.warnings)


class TestProjectScopedStandup:
    """The planning→standup edge: sprint/roster context from the project's plan."""

    def _seed_project(self, db_path, *, link_standup: bool):
        from yeaboi.agent.state import Sprint
        from yeaboi.projects.store import ProjectStore

        sprint = Sprint(id="SP-1", name="Sprint 1", goal="", capacity_points=10, story_ids=())
        with ProjectStore(db_path) as projects:
            project = projects.create("Apollo")
        pid = project["project_id"]
        with SessionStore(db_path) as s:
            s.create_session("plan-1", "Apollo", mode="planning", project_id=pid)
            s.save_state(
                "plan-1",
                {
                    "selected_team_members": ("Carol",),
                    "sprint_length_weeks": 3,
                    "sprints": [sprint],
                },
            )
            s.create_session("standup-1", "Apollo", project_id=pid if link_standup else "")
            s.save_state("standup-1", {"selected_team_members": ("Alice",), "sprint_length_weeks": 2})
        return pid

    def _run(self, monkeypatch, db_path, *, project_id=""):
        captured = {}

        def _capture_gather(state, **kw):
            captured["state"] = state
            return SprintContext(sprint_name="S", sprint_length_weeks=2)

        _patch_common(monkeypatch, items=[], counts=[])
        monkeypatch.setattr(engine.sprint_context, "gather", _capture_gather)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        engine.run_standup("standup-1", deliver=False, db_path=db_path, today=date(2026, 7, 10), project_id=project_id)
        return captured["state"]

    def test_explicit_project_uses_the_plans_state(self, monkeypatch, db_path):
        pid = self._seed_project(db_path, link_standup=False)
        state = self._run(monkeypatch, db_path, project_id=pid)
        assert state.get("sprint_length_weeks") == 3
        assert tuple(state.get("selected_team_members", ())) == ("Carol",)

    def test_linked_session_inherits_the_project(self, monkeypatch, db_path):
        self._seed_project(db_path, link_standup=True)
        state = self._run(monkeypatch, db_path)
        assert state.get("sprint_length_weeks") == 3

    def test_unlinked_session_keeps_its_own_state(self, monkeypatch, db_path):
        self._seed_project(db_path, link_standup=False)
        state = self._run(monkeypatch, db_path)
        assert state.get("sprint_length_weeks") == 2
        assert tuple(state.get("selected_team_members", ())) == ("Alice",)


class TestStandupContextDeps:
    """The plan toggle gates the sprint-plan substitution; the config column feeds it."""

    _seed_project = TestProjectScopedStandup._seed_project

    def _run(self, monkeypatch, db_path, *, project_id="", context_deps=None):
        captured = {}

        def _capture_gather(state, **kw):
            captured["state"] = state
            return SprintContext(sprint_name="S", sprint_length_weeks=2)

        _patch_common(monkeypatch, items=[], counts=[])
        monkeypatch.setattr(engine.sprint_context, "gather", _capture_gather)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        engine.run_standup(
            "standup-1",
            deliver=False,
            db_path=db_path,
            today=date(2026, 7, 10),
            project_id=project_id,
            context_deps=context_deps,
        )
        return captured["state"]

    def test_plan_dep_off_keeps_the_sessions_own_state(self, monkeypatch, db_path):
        pid = self._seed_project(db_path, link_standup=False)
        state = self._run(monkeypatch, db_path, project_id=pid, context_deps=["retro", "standup"])
        assert state.get("sprint_length_weeks") == 2

    def test_incognito_keeps_the_sessions_own_state(self, monkeypatch, db_path):
        pid = self._seed_project(db_path, link_standup=False)
        state = self._run(monkeypatch, db_path, project_id=pid, context_deps=[])
        assert state.get("sprint_length_weeks") == 2

    def test_explicit_deps_including_plan_still_substitute(self, monkeypatch, db_path):
        pid = self._seed_project(db_path, link_standup=False)
        state = self._run(monkeypatch, db_path, project_id=pid, context_deps=["plan"])
        assert state.get("sprint_length_weeks") == 3

    def test_saved_config_deps_apply_when_the_caller_passes_none(self, monkeypatch, db_path):
        self._seed_project(db_path, link_standup=True)
        with StandupStore(db_path) as store:
            store.save_config(
                "standup-1",
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                context_deps=["retro"],
            )
        state = self._run(monkeypatch, db_path)
        assert state.get("sprint_length_weeks") == 2

    def test_explicit_param_beats_the_saved_config(self, monkeypatch, db_path):
        self._seed_project(db_path, link_standup=True)
        with StandupStore(db_path) as store:
            store.save_config(
                "standup-1",
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                context_deps=["retro"],
            )
        state = self._run(monkeypatch, db_path, context_deps=["plan"])
        assert state.get("sprint_length_weeks") == 3


class TestProductionReachesTheRun:
    """Ops in the standup, and — the load-bearing half — ops absent from it."""

    ITEMS = [
        {
            "author": "Alice",
            "kind": "issue",
            "key": "YEA-12",
            "status": "Done",
            "title": "auth epic",
            "source": "jira",
            "url": "https://j/12",
        },
    ]

    EVENT = OpsEvent(
        kind="incident",
        source="pagerduty",
        ref="PD-4821",
        title="YEA-12 checkout is down",
        service="checkout",
        severity="high",
        status="triggered",
        started_at="2026-07-09T09:00:00Z",
        url="https://pd/4821",
    )

    def _connect(self, monkeypatch, *, events=(), errors=()):
        from yeaboi.connectors.fetching import Gathered, SourceResult
        from yeaboi.ops.signals import roll_up

        sources = [SourceResult(key="pagerduty", label="PagerDuty", family="incidents", ok=True, count=len(events))]
        sources += [SourceResult(key=k, label=k.title(), error=msg) for k, msg in errors]
        gathered = Gathered(
            since="14d",
            window_start="2026-06-26T00:00:00+00:00",
            window_end="2026-07-10T00:00:00+00:00",
            sources=tuple(sources),
            events=tuple(events),
            signals=roll_up(
                tuple(events),
                window_start="2026-06-26T00:00:00+00:00",
                window_end="2026-07-10T00:00:00+00:00",
            ),
        )
        monkeypatch.setattr("yeaboi.standup.ops.connected", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", lambda **kw: gathered)

    def test_nothing_connected_never_reaches_a_vendor(self, monkeypatch, db_path, seeded_session):
        # The §0 invariant at the engine: with no ops vendor connected there is
        # no fetch, no signal, and no phase announced for work not happening.
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        monkeypatch.setattr("yeaboi.standup.ops.connected", lambda: False)

        def _never(**kwargs):
            raise AssertionError("a vendor was contacted with nothing connected")

        monkeypatch.setattr("yeaboi.connectors.fetching.gather", _never)
        phases: list[str] = []
        report = engine.run_standup(
            seeded_session,
            deliver=False,
            db_path=db_path,
            today=date(2026, 7, 10),
            on_progress=phases.append,
        )
        assert report.ops_signals == ()
        assert not any("Production" in p for p in phases)

    def test_signals_land_on_the_report(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        self._connect(monkeypatch, events=(self.EVENT,))

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        (signal,) = report.ops_signals
        assert (signal.kind, signal.source, signal.count) == ("incident", "pagerduty", 1)
        assert signal.window_start == "2026-06-26T00:00:00+00:00"

    def test_a_live_incident_on_a_done_ticket_earns_a_card(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        self._connect(monkeypatch, events=(self.EVENT,))

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        (card,) = report.conflicts
        assert card.entity_id == "YEA-12"
        assert card.fingerprint.endswith(":ops_conflict")
        # Nobody is on the hook for an alert firing.
        assert card.members == ()

    def test_the_roll_up_is_chained_with_its_handles(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        self._connect(monkeypatch, events=(self.EVENT,))

        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        from yeaboi.provenance import ProvenanceChain

        with ProvenanceChain(db_path) as chain:
            assert chain.verify().valid is True
            record = chain.get("standup:2026-07-10:production:pagerduty:incident")
            assert record is not None
            assert record.inputs == ("pagerduty:PD-4821",)

    def test_a_connected_vendor_that_failed_is_a_notice(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        self._connect(monkeypatch, events=(), errors=[("datadog", "rate limited — try a shorter window")])

        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert any("Datadog: rate limited" in w for w in report.warnings)

    def test_an_ops_failure_never_costs_the_standup(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        monkeypatch.setattr("yeaboi.standup.ops.connected", lambda: True)

        def _boom(**kwargs):
            raise RuntimeError("network is down")

        monkeypatch.setattr("yeaboi.connectors.fetching.gather", _boom)
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert report.ops_signals == ()
        assert report.member_updates

    def test_production_never_enters_the_member_payload(self, monkeypatch, db_path, seeded_session):
        # _for_llm is the member evidence path; an ops event has no author to
        # belong to, so it must reach the model only as its own top-level block.
        _patch_common(monkeypatch, items=self.ITEMS, counts=[("jira", 1)])
        self._connect(monkeypatch, events=(self.EVENT,))
        captured: dict = {}

        def _fake_prompt(**kwargs):
            captured.update(kwargs)
            return "prompt"

        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.prompts.standup.get_standup_summary_prompt", _fake_prompt)
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json",
            lambda *a, **k: type("R", (), {"content": '{"members": [], "team_summary": "ok"}'})(),
        )
        engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))

        assert captured["production"] == [
            {
                "kind": "incident",
                "source": "pagerduty",
                "count": 1,
                "resolved": 0,
                "worst_severity": "high",
                "services": ["checkout"],
                "examples": ["YEA-12 checkout is down"],
            }
        ]
        assert captured["production_window"] == "the last 14 days"
        assert "pagerduty" not in json.dumps(captured["members"])


class TestSoloRun:
    """``solo=True`` is the Solo world: one card, no roster discovery, first-person notes."""

    def _llm(self, monkeypatch, members_json):
        llm_json = json.dumps({"members": members_json, "team_summary": "I shipped the login page."})
        monkeypatch.setattr(
            "yeaboi.agent.llm.get_llm",
            lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(llm_json)})(),
        )

    def _no_discovery(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            "yeaboi.standup.roster.discover_team_members", lambda *a, **k: calls.append(a) or ["Alice", "Zed"]
        )
        return calls

    def test_solo_run_is_self_only_and_never_discovers_a_roster(self, monkeypatch, db_path, seeded_session):
        calls = self._no_discovery(monkeypatch)
        _patch_common(
            monkeypatch,
            items=[{"author": "Me", "kind": "commit", "title": "login", "source": "github"}],
            counts=[("github", 1)],
        )
        self._llm(monkeypatch, [{"name": "Me", "summary": "Shipped login"}])
        report = engine.run_standup(seeded_session, deliver=False, solo=True, db_path=db_path, today=date(2026, 7, 10))
        # The plan's Alice/Bob never get a card, and the tracker is never asked.
        assert [m.name for m in report.member_updates] == ["Me"]
        assert calls == []
        assert report.solo is True

    def test_solo_ignores_an_explicit_roster(self, monkeypatch, db_path, seeded_session):
        self._no_discovery(monkeypatch)
        _patch_common(monkeypatch, items=[], counts=[])
        self._llm(monkeypatch, [{"name": "Me", "summary": "quiet day"}])
        report = engine.run_standup(
            seeded_session,
            deliver=False,
            solo=True,
            team_members=["Alice", "Bob"],
            db_path=db_path,
            today=date(2026, 7, 10),
        )
        assert [m.name for m in report.member_updates] == ["Me"]

    def test_solo_drops_other_authors_activity(self, monkeypatch, db_path, seeded_session):
        self._no_discovery(monkeypatch)
        _patch_common(
            monkeypatch,
            items=[
                {"author": "Me", "kind": "commit", "title": "login", "source": "github"},
                {"author": "Alice", "kind": "pr", "title": "refactor", "source": "github"},
            ],
            counts=[("github", 2)],
        )
        self._llm(monkeypatch, [{"name": "Me", "summary": "login"}])
        report = engine.run_standup(seeded_session, deliver=False, solo=True, db_path=db_path, today=date(2026, 7, 10))
        assert report.activity_counts == (("github", 1),)
        assert "Alice" not in [m.name for m in report.member_updates]

    def test_solo_reaches_the_prompt(self, monkeypatch, db_path, seeded_session):
        self._no_discovery(monkeypatch)
        _patch_common(
            monkeypatch,
            items=[{"author": "Me", "kind": "commit", "title": "login", "source": "github"}],
            counts=[("github", 1)],
        )
        self._llm(monkeypatch, [{"name": "Me", "summary": "Shipped login"}])
        seen: dict = {}
        from yeaboi.prompts import standup as prompts

        real = prompts.get_standup_summary_prompt

        def spy(**kw):
            seen["solo"] = kw.get("solo")
            return real(**kw)

        monkeypatch.setattr(prompts, "get_standup_summary_prompt", spy)
        engine.run_standup(seeded_session, deliver=False, solo=True, db_path=db_path, today=date(2026, 7, 10))
        assert seen["solo"] is True

    def test_a_team_run_is_unchanged(self, monkeypatch, db_path, seeded_session):
        _patch_common(monkeypatch, items=[], counts=[])
        self._llm(monkeypatch, [])
        report = engine.run_standup(seeded_session, deliver=False, db_path=db_path, today=date(2026, 7, 10))
        assert report.solo is False
        assert "Alice" in [m.name for m in report.member_updates]
