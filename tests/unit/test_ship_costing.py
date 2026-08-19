"""Tests for per-run transcript costing (ship/costing.py).

The fixture mirrors the real Claude Code JSONL shape the agentwatch collector
parses — including the requestId dedup trap and a planted fake secret that
must surface as a finding (label + line only, never content).
"""

from __future__ import annotations

import json

import pytest

from yeaboi.ship import costing

PLANTED_SECRET = "sk-ant-PLANTED000FAKE111SECRET222"


def _assistant(request_id, *, usage=None):
    return {
        "type": "assistant",
        "requestId": request_id,
        "timestamp": "2026-08-07T10:00:00.000Z",
        "sessionId": "sess-run",
        "message": {
            "role": "assistant",
            "model": "claude-opus-5",
            "usage": usage
            or {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 0,
                "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
            },
            "content": [{"type": "text", "text": "working"}],
        },
    }


def _write_transcript(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


class TestCostTranscript:
    def test_prices_deduped_usage(self, tmp_path):
        transcript = tmp_path / "sess-run.jsonl"
        # The same requestId twice — one API response split across lines must
        # be priced once.
        _write_transcript(transcript, [_assistant("req-1"), _assistant("req-1"), _assistant("req-2")])
        cost = costing.cost_transcript(transcript)
        assert cost is not None
        assert cost.session_id == "sess-run"
        assert cost.known_models
        # Opus 5: (1000/1e6)*5 + (500/1e6)*25 per unique request = 0.0175 × 2.
        assert cost.usd == pytest.approx(0.035)

    def test_unknown_model_is_reported_not_hidden(self, tmp_path):
        transcript = tmp_path / "sess-run.jsonl"
        record = _assistant("req-1")
        record["message"]["model"] = "future-model-x"
        _write_transcript(transcript, [record])
        cost = costing.cost_transcript(transcript)
        assert cost is not None
        assert not cost.known_models
        assert cost.usd > 0  # priced at the fallback tier, not zero

    def test_planted_secret_becomes_a_finding_without_content(self, tmp_path):
        transcript = tmp_path / "sess-run.jsonl"
        _write_transcript(
            transcript,
            [
                {
                    "type": "user",
                    "sessionId": "sess-run",
                    "message": {"role": "user", "content": f"my key is {PLANTED_SECRET}"},
                },
                _assistant("req-1"),
            ],
        )
        cost = costing.cost_transcript(transcript)
        assert cost is not None
        assert any(f.kind == "secret" for f in cost.findings)
        for finding in cost.findings:
            assert PLANTED_SECRET not in finding.label

    def test_unreadable_file_returns_none(self, tmp_path):
        assert costing.cost_transcript(tmp_path / "missing.jsonl") is None

    def test_malformed_lines_do_not_break_pricing(self, tmp_path):
        transcript = tmp_path / "sess-run.jsonl"
        transcript.write_text("{broken\n" + json.dumps(_assistant("req-1")) + "\n", encoding="utf-8")
        cost = costing.cost_transcript(transcript)
        assert cost is not None
        assert cost.usd > 0


class TestLocateTranscript:
    def test_finds_the_file_named_after_the_session(self, tmp_path, monkeypatch):
        root = tmp_path / "projects"
        (root / "some-proj").mkdir(parents=True)
        target = root / "some-proj" / "sess-42.jsonl"
        target.write_text("", encoding="utf-8")
        monkeypatch.setattr(costing, "_source_roots", lambda: (("claude_code", root),))
        assert costing.locate_transcript("sess-42") == target
        assert costing.locate_transcript("sess-404") is None

    def test_rejects_path_shaped_session_ids(self, tmp_path, monkeypatch):
        monkeypatch.setattr(costing, "_source_roots", lambda: (("claude_code", tmp_path),))
        assert costing.locate_transcript("../../etc/passwd") is None
        assert costing.locate_transcript("") is None

    def test_rejects_glob_shaped_session_ids(self, tmp_path, monkeypatch):
        # The id comes from the agent's own envelope and lands in an rglob
        # pattern — a wildcard would match, and price, an unrelated run.
        root = tmp_path / "projects"
        (root / "some-proj").mkdir(parents=True)
        (root / "some-proj" / "sess-42.jsonl").write_text("", encoding="utf-8")
        monkeypatch.setattr(costing, "_source_roots", lambda: (("claude_code", root),))
        assert costing.locate_transcript("*") is None
        assert costing.locate_transcript("sess-4?") is None
        assert costing.locate_transcript("sess-[0-9]*") is None
