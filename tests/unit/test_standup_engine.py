"""Unit tests for the standup engine pipeline (mocked LLM + sources)."""

import json
from datetime import date

import pytest

from yeaboi.agent.state import MemberUpdate, StandupGap, TranscriptClaim, TranscriptReview
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

        def fake_deliver(report, channels):
            delivered["channels"] = channels
            return {c: True for c in channels}

        import yeaboi.standup.delivery as delivery_mod

        monkeypatch.setattr(delivery_mod, "deliver", fake_deliver)
        engine.run_standup(
            seeded_session, deliver=True, channels=["terminal"], db_path=db_path, today=date(2026, 7, 10)
        )
        assert delivered["channels"] == ["terminal"]


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
        # Jira ran but GitHub/AzDO were not set up → the report itself must say
        # so (⚠ Notices) and advise connecting them, not just the Activity detail.
        from yeaboi.standup.collector import ActivityBundle

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
        assert "Github (STANDUP_GITHUB_REPO not set)" in notice
        assert "Azure Devops (AZURE_DEVOPS_PROJECT not set)" in notice
        assert "connect these in .env" in notice
        assert notice == report.warnings[-1]  # advisory, so auth/LLM problems stay on top

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

    def test_urlless_items_survive_and_dedupe_by_identity(self):
        # Unlike _member_links, an in-progress ticket with no URL still says something.
        acts = [
            {"kind": "wip", "title": "Widen audit windows", "key": "PSOT-1613"},
            {"kind": "wip", "title": "Widen audit windows", "key": "PSOT-1613"},
        ]
        rows = engine._member_evidence(acts)
        assert len(rows) == 1
        assert rows[0].url == ""

    def test_caps_at_eight_preserving_order(self):
        acts = [{"kind": "pr", "title": f"pr {i}", "key": f"#{i}", "url": f"https://g/pr/{i}"} for i in range(12)]
        rows = engine._member_evidence(acts)
        assert len(rows) == 8
        assert rows[0].key == "#0" and rows[7].key == "#7"

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
