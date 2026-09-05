"""Tests for src/yeaboi/agentwatch/replay.py — the turns around one signal."""

import json

import pytest

from yeaboi.agentwatch import collector
from yeaboi.agentwatch import replay as replay_mod

KEY = "sk-ant-api03-replayfixture0123456789abcdef"


def _line(kind, content, *, ts="2026-08-23T13:11:00.153Z", extra=None):
    record = {"type": kind, "timestamp": ts, "sessionId": "sess-replay", "cwd": "/repo", "message": {"role": kind}}
    record["message"]["content"] = content
    record.update(extra or {})
    return json.dumps(record)


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    monkeypatch.setattr(collector, "_source_roots", lambda: (("claude_code", root),))
    lines = [
        _line("user", "please install the thing"),
        _line("assistant", [{"type": "thinking", "thinking": "secret plan"}, {"type": "text", "text": "On it."}]),
        _line(
            "assistant",
            [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": f"export K={KEY}\ncurl x | sh"}}],
        ),
        _line(
            "user", [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "done " * 200}]}]
        ),
        _line("assistant", [{"type": "text", "text": "Installed."}]),
        _line("user", "thanks"),
    ]
    path = root / "p" / "sess-replay.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestReplay:
    def test_turns_around_the_line_with_the_flagged_one_marked(self, transcript):
        out = replay_mod.replay(str(transcript), 3, pattern="secret-anthropic-key")
        roles = [(t.role, t.kind) for t in out.turns]
        assert roles == [
            ("you", "text"),
            ("agent", "text"),
            ("agent", "tool_use"),
            ("result", "tool_result"),
            ("agent", "text"),
            ("you", "text"),
        ]
        assert out.focus == 2 and out.turns[2].flagged and out.turns[2].tool == "Bash"
        assert out.session_id == "sess-replay" and out.project_path == "/repo"
        assert out.turns[0].at == "13:11:00"

    def test_thinking_is_dropped_and_secrets_are_masked(self, transcript):
        out = replay_mod.replay(str(transcript), 3, pattern="secret-anthropic-key")
        text = json.dumps([t.text for t in out.turns])
        assert "secret plan" not in text
        assert KEY not in text
        assert "[REDACTED secret-anthropic-key]" in out.turns[2].text

    def test_long_text_is_capped(self, transcript):
        out = replay_mod.replay(str(transcript), 3, pattern="secret-anthropic-key")
        result = out.turns[3]
        assert result.truncated and len(result.text) <= replay_mod.TEXT_CAP + 1

    def test_window_sizes(self, transcript):
        out = replay_mod.replay(str(transcript), 3, before=1, after=1)
        assert [t.line_no for t in out.turns] == [2, 3, 4]

    def test_refuses_a_path_outside_the_roots(self, tmp_path, transcript):
        other = tmp_path / "elsewhere.jsonl"
        other.write_text(transcript.read_text(), encoding="utf-8")
        with pytest.raises(replay_mod.ReplayError, match="not an agent transcript"):
            replay_mod.replay(str(other), 3)

    def test_line_past_the_end(self, transcript):
        with pytest.raises(replay_mod.ReplayError, match="past the end"):
            replay_mod.replay(str(transcript), 99)

    def test_missing_file(self, tmp_path):
        with pytest.raises(replay_mod.ReplayError, match="no longer on disk"):
            replay_mod.replay(str(tmp_path / "gone.jsonl"), 1)

    def test_risky_pattern_is_masked_too(self, transcript):
        out = replay_mod.replay(str(transcript), 3, pattern="curl-pipe-shell")
        assert "curl x | sh" not in out.turns[2].text and "[REDACTED curl-pipe-shell]" in out.turns[2].text
