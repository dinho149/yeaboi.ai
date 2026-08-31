"""Tests for the Privacy page: the builder (_build_privacy_screen) and the
toggle half of its runner (_run_privacy_page)."""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._screens_secondary import _build_privacy_screen


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height + 5, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


class TestBuildPrivacyScreen:
    def test_returns_panel(self):
        assert isinstance(_build_privacy_screen(width=80, height=24), Panel)

    def test_respects_exact_height(self):
        out = _render(_build_privacy_screen(width=80, height=24), width=80, height=24)
        assert len(out.splitlines()) == 24

    def test_shows_the_headline(self):
        from yeaboi.privacy import PRIVACY_HEADLINE

        out = _render(_build_privacy_screen(width=100, height=40))
        assert PRIVACY_HEADLINE in out

    def test_scrolled_view_reaches_the_disclosures(self):
        # The egress table sits under the statement; scrolling far enough must
        # bring the off-switch column into the viewport.
        out = _render(_build_privacy_screen(scroll_offset=200, width=120, height=40), width=120, height=40)
        assert "Off-switch:" in out

    def test_scroll_meta_is_published(self):
        meta: dict = {}
        _build_privacy_screen(scroll_meta=meta, width=80, height=24)
        assert meta["max_offset"] > 0
        assert meta["viewport_h"] >= 1

    def test_every_group_header_renders(self):
        from yeaboi.privacy import EGRESS_GROUPS

        full = "".join(
            _render(_build_privacy_screen(scroll_offset=offset, width=120, height=40), width=120, height=40)
            for offset in (0, 20, 40, 60)
        )
        for group in EGRESS_GROUPS:
            assert group["title"].upper() in full, group["key"]

    def test_state_chips_render(self):
        full = "".join(
            _render(_build_privacy_screen(scroll_offset=offset, width=120, height=40), width=120, height=40)
            for offset in (0, 20, 40, 60)
        )
        for chip in (" ON ", " ON USE ", " OFF ", " YOU SEND "):
            assert chip in full

    def test_key_hint_footer_renders(self):
        out = _render(_build_privacy_screen(width=100, height=40))
        assert "enter toggle" in out
        assert "esc back" in out

    def test_focus_meta_names_every_unique_switch(self):
        from yeaboi.privacy import EGRESS_SWITCHES

        meta: dict = {}
        _build_privacy_screen(scroll_meta=meta, width=100, height=40)
        envs = [env for env, _ in meta["focus_envs"]]
        assert envs == list(dict.fromkeys(entry["env"] for entry in EGRESS_SWITCHES))
        assert len(meta["focus_lines"]) == len(envs)

    def test_live_chip_follows_the_env(self, monkeypatch):
        monkeypatch.delenv("YEABOI_UPDATE_CHECK", raising=False)
        on = _render(_build_privacy_screen(scroll_offset=20, width=120, height=40), width=120, height=40)
        monkeypatch.setenv("YEABOI_UPDATE_CHECK", "false")
        off = _render(_build_privacy_screen(scroll_offset=20, width=120, height=40), width=120, height=40)
        row = "A version query, carrying no identifiers"
        on_line = next(ln for ln in on.splitlines() if row in ln)
        off_line = next(ln for ln in off.splitlines() if row in ln)
        assert " ON " in on_line
        assert " OFF " in off_line

    def test_status_line_renders(self):
        out = _render(_build_privacy_screen(width=100, height=40, status="Telemetry updated"))
        assert "Telemetry updated" in out

    def test_alias_values_resolve_like_the_owning_modules(self, monkeypatch):
        # The exact spellings the page's own copy instructs must not fold back
        # to the field default (YEABOI_NO_TUNNEL=1, YEABOI_UPDATE_CHECK=off).
        from yeaboi.ui.mode_select.screens._screens_secondary import privacy_switch_is_on

        monkeypatch.setenv("YEABOI_UPDATE_CHECK", "off")
        assert privacy_switch_is_on("YEABOI_UPDATE_CHECK", "true") is False
        monkeypatch.setenv("YEABOI_NO_TUNNEL", "1")
        assert privacy_switch_is_on("YEABOI_NO_TUNNEL", "false") is False
        monkeypatch.setenv("YEABOI_TELEMETRY", "1")
        assert privacy_switch_is_on("YEABOI_TELEMETRY", "true") is True
        monkeypatch.setenv("YEABOI_TELEMETRY", "maybe")  # unrecognised → default off
        assert privacy_switch_is_on("YEABOI_TELEMETRY", "true") is False

    def test_focus_index_is_accepted(self):
        # The stripe is a background colour, invisible to plain-text capture —
        # assert the focused render still lays out and publishes anchors.
        meta: dict = {}
        out = _render(_build_privacy_screen(scroll_meta=meta, focus_index=0, width=100, height=40))
        assert meta["focus_lines"]
        assert len(out.splitlines()) >= 40


class TestRunPrivacyPage:
    """The toggle half of the runner: tab focuses, enter writes through the engine."""

    def _run(self, keys, monkeypatch, writes):
        from yeaboi.settings.engine import SettingWrite
        from yeaboi.ui.mode_select import _run_privacy_page

        def fake_set_setting(key, value):
            writes.append((key, value))
            return SettingWrite(ok=True, key=key, message=f"{key} updated")

        monkeypatch.setattr("yeaboi.settings.engine.set_setting", fake_set_setting)

        class _Live:
            def __init__(self):
                self.panels = []

            def update(self, panel):
                self.panels.append(panel)

        console = Console(file=io.StringIO(), width=100, height=32, legacy_windows=False)
        script = list(keys)
        live = _Live()
        _run_privacy_page(console, live, lambda timeout=None: script.pop(0), 0.05, False)
        return live

    def test_tab_enter_flips_the_first_switch(self, monkeypatch):
        monkeypatch.delenv("YEABOI_UPDATE_CHECK", raising=False)
        writes: list = []
        live = self._run(["tab", "enter", "esc"], monkeypatch, writes)
        # Update check defaults on; the flip writes the literal opt-out.
        assert writes == [("YEABOI_UPDATE_CHECK", "false")]
        assert live.panels  # the loop rendered

    def test_enter_without_focus_writes_nothing(self, monkeypatch):
        writes: list = []
        self._run(["enter", " ", "esc"], monkeypatch, writes)
        assert writes == []
