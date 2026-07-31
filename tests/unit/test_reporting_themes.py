"""Unit tests for the Reporting palette registry (built-ins + custom themes)."""

import json
import re

import pytest

from yeaboi.reporting import themes


@pytest.fixture
def themes_file(tmp_path, monkeypatch):
    path = tmp_path / "reporting_themes.json"
    monkeypatch.setattr("yeaboi.paths.get_reporting_themes_path", lambda: path)
    return path


_HEX = re.compile(r"^#[0-9a-f]{6}$")


class TestBuiltins:
    def test_four_builtins_with_valid_hex_roles(self):
        assert set(themes.BUILTIN_PALETTES) == {"midnight", "aurora", "sunset", "mono"}
        for palette in themes.BUILTIN_PALETTES.values():
            assert set(palette) == set(themes.ROLE_KEYS)
            for value in palette.values():
                assert _HEX.match(value), value

    def test_the_deck_reads_these_hexes_rather_than_its_own_copy(self):
        """There used to be a second copy of every palette here.

        The deck carried a matching `[data-theme="aurora"] { --bg1: … }` CSS
        block per built-in, and the guard in this slot compared the two lists
        hex by hex. The deck ships the registry itself now — palettes travel in
        the boot payload, which is also the only way a *custom* palette could
        ever have worked without generating a stylesheet — so there is one list
        again and nothing left to compare it against. This asserts the arrow
        instead: whatever is in here is what an exported deck paints with.
        """
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.reporting.presentation import deck_payload

        shipped = deck_payload(DeliveryReport(period_label="Last sprint"))["palettes"]
        for name, palette in themes.BUILTIN_PALETTES.items():
            assert shipped[name] == palette


class TestCustomPalettes:
    def test_missing_file_returns_empty(self, themes_file):
        assert themes.load_custom_palettes() == {}

    def test_round_trip_with_role_fill(self, themes_file):
        themes_file.write_text(json.dumps({"corporate": {"accent": "#2F81F7"}}), encoding="utf-8")
        loaded = themes.load_custom_palettes()
        assert loaded["corporate"]["accent"] == "#2f81f7"  # normalised to lowercase
        # Missing roles filled from midnight so a partial theme still renders.
        assert loaded["corporate"]["bg1"] == themes.BUILTIN_PALETTES["midnight"]["bg1"]

    def test_invalid_json_is_tolerated(self, themes_file):
        themes_file.write_text("{not json", encoding="utf-8")
        assert themes.load_custom_palettes() == {}

    def test_non_object_top_level_is_tolerated(self, themes_file):
        themes_file.write_text(json.dumps(["midnight"]), encoding="utf-8")
        assert themes.load_custom_palettes() == {}

    def test_bad_hex_skips_theme(self, themes_file):
        themes_file.write_text(json.dumps({"bad": {"accent": "blue"}}), encoding="utf-8")
        assert themes.load_custom_palettes() == {}

    def test_builtin_shadowing_is_skipped(self, themes_file):
        themes_file.write_text(json.dumps({"midnight": {"accent": "#ff0000"}}), encoding="utf-8")
        assert themes.load_custom_palettes() == {}
        assert themes.get_palette("midnight")["accent"] == themes.BUILTIN_PALETTES["midnight"]["accent"]

    def test_invalid_name_is_skipped(self, themes_file):
        themes_file.write_text(json.dumps({"Bad Name!": {"accent": "#ff0000"}}), encoding="utf-8")
        assert themes.load_custom_palettes() == {}

    def test_unknown_role_is_ignored_not_fatal(self, themes_file):
        themes_file.write_text(json.dumps({"corporate": {"accent": "#2f81f7", "sparkle": "#ffffff"}}), encoding="utf-8")
        loaded = themes.load_custom_palettes()
        assert "corporate" in loaded
        assert "sparkle" not in loaded["corporate"]


class TestLookups:
    def test_all_palettes_lists_builtins_first(self, themes_file):
        themes_file.write_text(json.dumps({"corporate": {"accent": "#2f81f7"}}), encoding="utf-8")
        names = list(themes.all_palettes())
        assert names[:4] == ["midnight", "aurora", "sunset", "mono"]
        assert names[4:] == ["corporate"]
        assert themes.all_theme_names() == tuple(names)

    def test_get_palette_falls_back_to_midnight(self, themes_file):
        assert themes.get_palette("does-not-exist") == themes.BUILTIN_PALETTES["midnight"]

    def test_is_valid_theme(self, themes_file):
        themes_file.write_text(json.dumps({"corporate": {"accent": "#2f81f7"}}), encoding="utf-8")
        assert themes.is_valid_theme("midnight")
        assert themes.is_valid_theme("corporate")
        assert not themes.is_valid_theme("nope")
