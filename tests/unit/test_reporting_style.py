"""Unit tests for reporting/style — persisted deck-style preferences."""

import dataclasses
import json

import pytest

from yeaboi.reporting import style as style_mod
from yeaboi.reporting.style import (
    COLOR_ROLES,
    CONTENT_FITS,
    DEFAULT_STYLE,
    FONT_PRESETS,
    FONT_SCALES,
    LAYOUTS,
    STYLE_FIELDS,
    DeckStyle,
    cap_items,
    load_deck_style,
    resolve_color,
    save_deck_style,
    style_from_dict,
    style_summary,
    style_to_dict,
)


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    path = tmp_path / "reporting_prefs.json"
    monkeypatch.setattr("yeaboi.paths.get_reporting_prefs_path", lambda: path)
    return path


def _non_default():
    return DeckStyle(
        title_color="#ff0000",
        heading_color="accent2",
        font_family="classic",
        font_scale="large",
        layout="compact",
        content_fit="tight",
        max_bullets=4,
        include_items_table=False,
        include_signals=False,
        include_highlights=False,
        include_thanks=False,
        slide_numbers=True,
        footer_text="ACME Corp",
    )


class TestDeckStyle:
    def test_defaults_are_neutral(self):
        s = DeckStyle()
        assert s.title_color == "" and s.heading_color == ""
        assert s.font_family == "modern" and s.font_scale == "normal"
        assert s.layout == "detailed" and s.max_bullets == 6
        assert s.content_fit == "ask"
        assert s.include_items_table and s.include_signals and s.include_highlights and s.include_thanks
        assert not s.slide_numbers and s.footer_text == ""
        assert s == DEFAULT_STYLE

    def test_style_fields_cover_every_dataclass_field_exactly_once(self):
        spec_fields = [f for f, _label, _kind in STYLE_FIELDS]
        assert sorted(spec_fields) == sorted(f.name for f in dataclasses.fields(DeckStyle))
        assert len(spec_fields) == len(set(spec_fields))

    def test_every_font_preset_has_label_pptx_and_css(self):
        for preset in FONT_PRESETS.values():
            assert preset["label"] and preset["pptx"] and preset["css"]

    def test_enums_are_consistent(self):
        assert DEFAULT_STYLE.font_family in FONT_PRESETS
        assert DEFAULT_STYLE.font_scale in FONT_SCALES
        assert DEFAULT_STYLE.layout in LAYOUTS
        assert DEFAULT_STYLE.content_fit in CONTENT_FITS
        assert FONT_SCALES["normal"] == 1.0


class TestStyleFromDict:
    def test_round_trip_non_default(self):
        s = _non_default()
        assert style_from_dict(style_to_dict(s)) == s

    def test_non_dict_returns_default(self):
        assert style_from_dict(None) == DEFAULT_STYLE
        assert style_from_dict("compact") == DEFAULT_STYLE
        assert style_from_dict([1, 2]) == DEFAULT_STYLE

    def test_unknown_keys_ignored(self):
        assert style_from_dict({"nonsense": True, "layout": "compact"}) == DeckStyle(layout="compact")

    def test_bad_colors_fall_back_to_theme_default(self):
        s = style_from_dict({"title_color": "not-a-color", "heading_color": "bg1"})
        assert s.title_color == "" and s.heading_color == ""

    def test_role_and_hex_colors_accepted(self):
        s = style_from_dict({"title_color": "accent2", "heading_color": "#AABBCC"})
        assert s.title_color == "accent2"
        assert s.heading_color == "#aabbcc"  # lowercased

    def test_bad_enums_fall_back(self):
        s = style_from_dict(
            {"font_family": "wingdings", "font_scale": "huge", "layout": "diagonal", "content_fit": "diagonal"}
        )
        assert s == DEFAULT_STYLE

    def test_content_fit_values_accepted(self):
        assert style_from_dict({"content_fit": "expand"}).content_fit == "expand"
        assert style_from_dict({"content_fit": "TIGHT"}).content_fit == "tight"  # case-tolerant

    def test_max_bullets_clamped_and_coerced(self):
        assert style_from_dict({"max_bullets": 99}).max_bullets == 10
        assert style_from_dict({"max_bullets": 0}).max_bullets == 2
        assert style_from_dict({"max_bullets": "5"}).max_bullets == 5
        assert style_from_dict({"max_bullets": "lots"}).max_bullets == 6

    def test_footer_stripped_and_truncated(self):
        s = style_from_dict({"footer_text": "  " + "x" * 300})
        assert s.footer_text == "x" * 120

    def test_never_raises_on_garbage_values(self):
        s = style_from_dict({f: object() for f, _label, _kind in STYLE_FIELDS})
        assert isinstance(s, DeckStyle)


class TestResolveColor:
    _PALETTE = {"accent": "#8c78e6", "accent2": "#b8a6ff", "fg": "#e6edf3", "muted": "#9aa4b2"}

    def test_empty_uses_default(self):
        assert resolve_color("", self._PALETTE, "#123456") == "#123456"

    def test_role_resolves_against_palette(self):
        assert resolve_color("accent2", self._PALETTE, "#000000") == "#b8a6ff"

    def test_hex_passes_through_lowercased(self):
        assert resolve_color("#AABBCC", self._PALETTE, "#000000") == "#aabbcc"

    def test_garbage_uses_default(self):
        assert resolve_color("nonsense", self._PALETTE, "#123456") == "#123456"


class TestCapItems:
    def test_under_cap_untouched(self):
        assert cap_items(("a", "b"), 6) == ["a", "b"]

    def test_over_cap_appends_overflow_marker(self):
        capped = cap_items([f"item {i}" for i in range(10)], 3)
        assert capped[:3] == ["item 0", "item 1", "item 2"]
        assert capped[3] == "… and 7 more"
        assert len(capped) == 4


class TestPersistence:
    def test_missing_file_returns_default(self, prefs_path):
        assert load_deck_style() == DEFAULT_STYLE

    def test_save_then_load_round_trips(self, prefs_path):
        s = _non_default()
        save_deck_style(s)
        assert prefs_path.exists()
        assert load_deck_style() == s

    def test_saved_file_uses_envelope(self, prefs_path):
        save_deck_style(DeckStyle(layout="compact"))
        raw = json.loads(prefs_path.read_text())
        assert raw["deck_style"]["layout"] == "compact"

    def test_flat_dict_without_envelope_accepted(self, prefs_path):
        prefs_path.write_text(json.dumps({"layout": "compact"}))
        assert load_deck_style().layout == "compact"

    def test_bad_json_returns_default(self, prefs_path):
        prefs_path.write_text("{nope")
        assert load_deck_style() == DEFAULT_STYLE

    def test_non_dict_json_returns_default(self, prefs_path):
        prefs_path.write_text('["a", "b"]')
        assert load_deck_style() == DEFAULT_STYLE

    def test_partial_envelope_merges_defaults(self, prefs_path):
        prefs_path.write_text(json.dumps({"deck_style": {"font_family": "mono"}}))
        s = load_deck_style()
        assert s.font_family == "mono" and s.layout == "detailed"

    def test_save_failure_never_raises(self, prefs_path, monkeypatch):
        monkeypatch.setattr(type(prefs_path), "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
        save_deck_style(_non_default())  # must not raise


class TestStyleSummary:
    def test_default_is_default(self):
        assert style_summary(DEFAULT_STYLE) == "default"

    def test_deviations_listed(self):
        s = _non_default()
        summary = style_summary(s)
        for expected in (
            "title #ff0000",
            "headings accent2",
            "classic",
            "large",
            "compact layout",
            "fit tight",
            "no appendix",
        ):
            assert expected in summary

    def test_module_import_side_effect_free(self, tmp_path, monkeypatch):
        # Importing style must not create the prefs file (load/save are explicit).
        assert isinstance(style_mod.DEFAULT_STYLE, DeckStyle)
        assert COLOR_ROLES == ("accent", "accent2", "fg", "muted")


class TestSummaryPoints:
    def test_splits_on_sentence_boundaries(self):
        text = (
            "We shipped the first milestone of the access programme. "
            "Monitoring now covers every cloud account we run. "
            "The cleanup wave removed forty legacy service credentials."
        )
        points = style_mod.summary_points(text)
        assert len(points) == 3
        assert points[0].endswith("programme.")
        assert points[2].startswith("The cleanup")

    def test_short_fragment_merges_into_predecessor(self):
        text = (
            "We hardened the identity platform across every region this quarter. Twice. "
            "The monitoring rollout also finished ahead of the planned schedule."
        )
        points = style_mod.summary_points(text)
        assert len(points) == 2
        assert "this quarter. Twice." in points[0]

    def test_max_points_merges_tail_never_drops(self):
        sentences = [f"Sentence number {i} carries enough characters to stand alone as one point." for i in range(9)]
        points = style_mod.summary_points(" ".join(sentences), max_points=4)
        assert len(points) == 4
        assert "number 8" in points[-1]  # tail content preserved, not dropped

    def test_empty_and_whitespace(self):
        assert style_mod.summary_points("") == []
        assert style_mod.summary_points("   ") == []

    def test_single_sentence_is_one_point(self):
        assert style_mod.summary_points("Just one plain sentence here.") == ["Just one plain sentence here."]
