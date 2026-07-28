"""Tests for the Usage page data collection (`_collect_usage_data`).

Focus: the local Ollama provider must report a real model name, a
"configured" API status (it needs no key), and a $0 cost — local models run
on the user's own hardware, so a fabricated cloud-priced cost would mislead.
"""

from __future__ import annotations

import yeaboi.ui.mode_select as mode_select
from yeaboi.ui.mode_select import _collect_usage_data


def _collect(monkeypatch, tmp_path, provider: str, **env: str) -> dict:
    """Run _collect_usage_data with a scratch DB and a controlled environment."""
    monkeypatch.setattr(mode_select, "_ana_dbp", tmp_path / "usage-test.db")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return _collect_usage_data()


class TestUsageDataOllama:
    def test_model_resolves_to_provider_default(self, monkeypatch, tmp_path):
        data = _collect(monkeypatch, tmp_path, "ollama")
        assert data["model"] == "qwen3:8b"

    def test_keyless_provider_shows_configured(self, monkeypatch, tmp_path):
        data = _collect(monkeypatch, tmp_path, "ollama")
        assert data["api_key_status"] == "configured"

    def test_cost_is_zero_for_local(self, monkeypatch, tmp_path):
        from yeaboi.agent.llm import reset_usage_stats, track_usage

        reset_usage_stats()
        try:
            from types import SimpleNamespace

            track_usage(SimpleNamespace(response_metadata={"usage": {"input_tokens": 1000, "output_tokens": 500}}))
            data = _collect(monkeypatch, tmp_path, "ollama")
            assert data["tokens"]["estimated_cost"] == 0.0
        finally:
            reset_usage_stats()


class TestLifetimeUsageByProvider:
    def _seed(self, db_path):
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as store:
            store.record_token_usage(1_000_000, 1_000_000, model="claude-sonnet-4-6", provider="anthropic")
            store.record_token_usage(2_000_000, 2_000_000, model="qwen3:8b", provider="ollama")

    def test_store_groups_by_provider(self, tmp_path):
        from yeaboi.sessions import SessionStore

        db = tmp_path / "usage.db"
        self._seed(db)
        with SessionStore(db) as store:
            usage = store.get_lifetime_usage_by_provider()
        assert usage["anthropic"]["input_tokens"] == 1_000_000
        assert usage["ollama"]["total_tokens"] == 4_000_000
        assert usage["anthropic"]["call_count"] == 1

    def test_mixed_history_prices_only_cloud_rows(self, monkeypatch, tmp_path):
        """Anthropic rows keep their real cost even when the CURRENT provider is
        the free local one — switching to Ollama must not hide past cloud spend."""
        db = tmp_path / "usage-test.db"
        self._seed(db)
        data = _collect(monkeypatch, tmp_path, "ollama")
        lt = data["lifetime_tokens"]
        assert lt["calls"] == 2
        assert lt["total"] == 6_000_000
        # 1M in @ $3/M + 1M out @ $15/M = $18 for the anthropic rows; ollama rows $0.
        assert lt["estimated_cost"] == 18.0


class TestLocalPerformanceSection:
    def _seed_perf(self, db_path):
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as store:
            store.record_token_usage(
                200,
                100,
                model="qwen3:8b",
                provider="ollama",
                duration_ms=2000.0,
                load_duration_ms=300.0,
                tokens_per_sec=45.0,
            )

    def test_local_performance_present_for_ollama_rows(self, monkeypatch, tmp_path):
        db = tmp_path / "usage-test.db"
        self._seed_perf(db)
        data = _collect(monkeypatch, tmp_path, "ollama")
        perf = data["local_performance"]
        assert perf["calls"] == 1
        assert perf["avg_tps"] == 45.0
        assert perf["last"]["model"] == "qwen3:8b"

    def test_local_performance_empty_for_cloud_only(self, monkeypatch, tmp_path):
        from yeaboi.sessions import SessionStore

        db = tmp_path / "usage-test.db"
        with SessionStore(db) as store:
            store.record_token_usage(100, 50, model="claude-sonnet-4-6", provider="anthropic")
        data = _collect(monkeypatch, tmp_path, "anthropic", ANTHROPIC_API_KEY="sk-ant-x")
        assert data["local_performance"] == {}

    def test_screen_renders_section_when_data_present(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        usage_data = {
            "provider": "ollama",
            "model": "qwen3:8b",
            "local_performance": {
                "calls": 3,
                "avg_tps": 42.0,
                "max_tps": 55.0,
                "avg_duration_ms": 1800.0,
                "avg_load_ms": 200.0,
                "last": {"model": "qwen3:8b", "tps": 55.0, "duration_ms": 900.0},
            },
        }
        panel = _build_usage_screen(usage_data, width=90, height=40)
        console = Console(file=StringIO(), width=100, height=40)
        console.print(panel)
        out = console.file.getvalue()
        assert "Local Model Performance" in out
        assert "tok/s" in out

    def test_screen_hides_section_when_no_data(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        panel = _build_usage_screen({"provider": "anthropic", "local_performance": {}}, width=90, height=40)
        console = Console(file=StringIO(), width=100, height=40)
        console.print(panel)
        assert "Local Model Performance" not in console.file.getvalue()

    def test_screen_renders_copy_hint_and_message(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        panel = _build_usage_screen(
            {"provider": "anthropic", "local_performance": {}},
            width=90,
            height=40,
            actions=["Copy", "Back"],
            message="Copied to clipboard",
        )
        console = Console(file=StringIO(), width=100, height=40)
        console.print(panel)
        out = console.file.getvalue()
        # The Copy/Back buttons were replaced by a keyboard hint.
        assert "copy" in out and "back" in out
        assert "Copied to clipboard" in out


class TestUsageDataCloud:
    def test_anthropic_without_key_not_configured(self, monkeypatch, tmp_path):
        data = _collect(monkeypatch, tmp_path, "anthropic")
        assert data["api_key_status"] == "not configured"

    def test_anthropic_with_key_configured(self, monkeypatch, tmp_path):
        data = _collect(monkeypatch, tmp_path, "anthropic", ANTHROPIC_API_KEY="sk-ant-x")
        assert data["api_key_status"] == "configured"
        assert data["model"] == "claude-sonnet-4-6"

    def test_cloud_cost_still_estimated(self, monkeypatch, tmp_path):
        from yeaboi.agent.llm import reset_usage_stats, track_usage

        reset_usage_stats()
        try:
            from types import SimpleNamespace

            track_usage(SimpleNamespace(response_metadata={"usage": {"input_tokens": 1000, "output_tokens": 500}}))
            data = _collect(monkeypatch, tmp_path, "anthropic", ANTHROPIC_API_KEY="sk-ant-x")
            assert data["tokens"]["estimated_cost"] > 0
        finally:
            reset_usage_stats()
