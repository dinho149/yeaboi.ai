"""Unit tests for the standup engine pipeline (mocked LLM + sources)."""

import json
from datetime import date

import pytest

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
        assert "url" not in alice["code_activity"][0] and "key" not in alice["code_activity"][0]

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
