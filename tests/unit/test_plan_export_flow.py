"""Tests for _plan_export_flow's scope parameter (plan / transcript / both)."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console

from yeaboi.ui.session.phases._phases import _plan_export_flow


def _console() -> Console:
    return Console(file=StringIO(), width=100, height=40, force_terminal=True)


def _key(timeout: float = 0.0) -> str:
    return "enter"


def _state() -> dict:
    return {
        "messages": [HumanMessage(content="build it"), AIMessage(content="Q1?")],
        "_chat_preamble": [{"role": "ai", "text": "Hey"}],
    }


@pytest.fixture
def _export_env(tmp_path, monkeypatch):
    import yeaboi.paths as paths
    from yeaboi import fs_policy
    from yeaboi.ui.shared import _export_picker

    monkeypatch.setattr(_export_picker, "pick_export_destination", lambda *a, **k: "files")
    monkeypatch.setattr(paths, "get_planning_export_dir", lambda slug: tmp_path)
    monkeypatch.setattr(fs_policy, "resolve_and_check", lambda path, **kw: path)
    # The success screen blocks ~1s on real time — skip the wait.
    monkeypatch.setattr(
        "yeaboi.ui.session.phases._phases.time",
        MagicMock(monotonic=MagicMock(side_effect=[0.0, 2.0, 2.0, 2.0])),
    )
    return tmp_path


class TestScopes:
    def test_transcript_scope_writes_only_chat_file(self, _export_env):
        _plan_export_flow(MagicMock(), _console(), _key, _state(), "complete", scope="transcript")
        assert (_export_env / "scrum-chat.md").exists()
        assert not (_export_env / "scrum-plan.md").exists()
        assert not (_export_env / "scrum-plan.html").exists()

    def test_both_scope_writes_all_three(self, _export_env):
        _plan_export_flow(MagicMock(), _console(), _key, _state(), "complete", scope="both")
        assert (_export_env / "scrum-chat.md").exists()
        assert (_export_env / "scrum-plan.md").exists()
        assert (_export_env / "scrum-plan.html").exists()

    def test_default_scope_is_plan_only(self, _export_env):
        # The two pre-existing call sites pass no scope — byte-identical behavior.
        _plan_export_flow(MagicMock(), _console(), _key, _state(), "complete")
        assert not (_export_env / "scrum-chat.md").exists()
        assert (_export_env / "scrum-plan.md").exists()

    def test_copy_destination_gets_transcript_markdown(self, _export_env, monkeypatch):
        from yeaboi.ui.shared import _export_picker

        monkeypatch.setattr(_export_picker, "pick_export_destination", lambda *a, **k: "copy")
        copied: list[str] = []
        with patch("yeaboi.clipboard.copy_markdown_status", side_effect=lambda md: copied.append(md) or "Copied"):
            _plan_export_flow(MagicMock(), _console(), _key, _state(), "complete", scope="transcript")
        assert len(copied) == 1
        assert "### You" in copied[0]

    def test_publish_destination_titles_transcript(self, _export_env, monkeypatch):
        from yeaboi.ui.shared import _export_picker

        monkeypatch.setattr(_export_picker, "pick_export_destination", lambda *a, **k: "notion")
        published: list[tuple] = []

        def fake_publish(dest, *, title, markdown):
            published.append((dest, title))
            return MagicMock(ok=True, url="https://notion.so/x", message="Published")

        with patch("yeaboi.export_targets.publish_markdown", side_effect=fake_publish):
            _plan_export_flow(MagicMock(), _console(), _key, _state(), "complete", scope="both")
        titles = [t for _d, t in published]
        assert any("Sprint Plan" in t for t in titles)
        assert any("Chat Transcript" in t for t in titles)
