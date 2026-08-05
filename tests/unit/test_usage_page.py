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
        # Copy/Back moved out of the body into the bottom-left chrome tabs, and
        # the toast moved out of the body too — the usage loop speaks it through
        # the shared duck voice, so the builder stamps nothing itself.
        assert panel._copy_tab is True
        assert getattr(panel, "_duck_say", "") == ""
        assert "Copied to clipboard" not in out


class TestUsageBoxedLayout:
    """The Usage page renders each section as its own bordered box, laid out in a
    grid whose column count follows the terminal width."""

    DATA = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6-20260101-a-very-long-model-identifier",
        "api_key_status": "configured",
        "lifetime_tokens": {
            "calls": 412,
            "input": 12_500_000,
            "output": 3_400_000,
            "total": 15_900_000,
            "estimated_cost": 42.1234,
        },
        "tokens": {"calls": 7, "input": 15000, "output": 3000, "total": 18000, "estimated_cost": 0.054},
        "local_performance": {
            "calls": 3,
            "avg_tps": 42.0,
            "max_tps": 55.0,
            "avg_duration_ms": 1800.0,
            "avg_load_ms": 200.0,
            "last": {"model": "qwen3:8b", "tps": 55.0},
        },
        "sessions": {"total": 12, "planning": 8, "analysis": 4, "last_used": "2026-07-28 10:30"},
        "version": "1.2.0",
        "python_version": "3.14.3",
        "langsmith": "enabled",
        "db_path": "~/.yeaboi/some/deeply/nested/directory/tree/sessions.db",
        "profiles": [{"name": "azdevops-PROJ", "source": "azdevops", "sprints": 8, "age": "3d ago"}],
    }

    @staticmethod
    def _render(width: int, height: int, *, data: dict | None = None, scroll: int = 0, **kw):
        """Render the page and return (plain rows, scroll_meta)."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        meta: dict = {}
        panel = _build_usage_screen(
            TestUsageBoxedLayout.DATA if data is None else data,
            width=width,
            height=height,
            scroll_offset=scroll,
            scroll_meta=meta,
            **kw,
        )
        buf = StringIO()
        Console(file=buf, width=width, height=height, force_terminal=False, legacy_windows=False).print(panel)
        rows = buf.getvalue().split("\n")
        if rows and rows[-1] == "":
            rows = rows[:-1]
        return rows, meta

    def test_fits_height_and_width_exactly(self):
        for width, height in ((200, 50), (120, 40), (90, 30), (84, 40), (80, 24)):
            rows, _ = self._render(width, height)
            assert len(rows) == height, f"{width}x{height} rendered {len(rows)} rows"
            assert max(len(r) for r in rows) <= width, f"{width}x{height} overflowed"

    def test_every_section_gets_its_own_box(self):
        rows, _ = self._render(200, 60)
        out = "\n".join(rows)
        for section in (
            "LLM Provider",
            "Lifetime Token Usage",
            "Current Session",
            "Local Model Performance",
            "Session History",
            "Environment",
            "Team Profiles",
        ):
            # Each title is drawn as a rounded box title: "╭─ Title ─…".
            assert f"─ {section} ─" in out, section
        assert "╭" in out and "╰" in out

    def test_column_count_adapts_to_width(self):
        def columns(width: int, height: int) -> int:
            rows, _ = self._render(width, height)
            # Count box top-left corners on the widest grid row.
            return max(r.count("╭") for r in rows)

        assert columns(200, 60) == 3
        assert columns(120, 60) == 3
        assert columns(90, 60) == 2
        assert columns(60, 40) == 1

    def test_long_values_crop_instead_of_wrapping(self):
        """A long model name / DB path must ellipsize — wrapping would give the box
        an unpredictable height and corrupt the grid."""
        rows, _ = self._render(120, 40)
        out = "\n".join(rows)
        assert "…" in out
        assert "claude-sonnet-4-6-20260101-a-very-long-model-identifier" not in out

    def test_scroll_offset_moves_the_rendered_lines(self):
        top, meta = self._render(90, 30)
        assert meta["max_offset"] > 0
        mid, _ = self._render(90, 30, scroll=3)
        assert mid != top
        bottom, _ = self._render(90, 30, scroll=meta["max_offset"])
        assert bottom != top
        # Past the end clamps to max_offset — no blank page, no crash.
        clamped, _ = self._render(90, 30, scroll=meta["max_offset"] + 50)
        assert clamped == bottom

    def test_scroll_meta_reports_flattened_line_geometry(self):
        """max_offset counts *rendered* lines of the boxed grid, not section rows."""
        _, meta = self._render(90, 30)
        tall, tall_meta = self._render(90, 60)
        assert meta["viewport_h"] < tall_meta["viewport_h"]
        assert tall_meta["max_offset"] == 0  # everything fits at 60 rows
        assert len(tall) == 60

    def test_sparse_data_still_renders(self):
        for data in ({}, {"provider": "ollama", "local_performance": {}}, {"provider": "ollama", "tokens": {}}):
            rows, _ = self._render(120, 40, data=data)
            assert len(rows) == 40
            assert "─ LLM Provider ─" in "\n".join(rows)


class TestRenderToLines:
    def test_flattens_a_multi_row_renderable(self):
        from rich.panel import Panel
        from rich.text import Text

        from yeaboi.ui.mode_select.screens._screens_secondary import _render_to_lines

        lines = _render_to_lines(Panel(Text("hi"), width=10, height=3), 20, ">>")
        assert len(lines) == 3
        assert all(line.plain.startswith(">>") for line in lines)
        assert "hi" in lines[1].plain


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
