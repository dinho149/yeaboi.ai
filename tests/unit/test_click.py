"""Tests for the shared mouse-click hit-testing helpers."""

from __future__ import annotations

import io

from rich.console import Console

from yeaboi.ui.shared._click import _button_runs, button_click, button_spans, parse_click
from yeaboi.ui.shared._components import PAD, build_action_buttons


def _console() -> Console:
    return Console(width=100, height=30, file=io.StringIO())


class TestParseClick:
    def test_parses_valid_click(self):
        assert parse_click("click:12:5") == (12, 5)

    def test_non_click_is_none(self):
        for k in ("enter", "left", "", "scroll_up", "paste:hello", "click"):
            assert parse_click(k) is None

    def test_malformed_coords_is_none(self):
        assert parse_click("click:x:y") is None


class TestButtonSpans:
    def test_spans_align_with_rendered_borders(self):
        # Every span's start/end column must land on the '│' of the rendered
        # button-mid row (content-relative coords, matching build_action_buttons).
        labels = ["Configure", "Log Level", "Data Dir", "Back"]
        _, mid, _ = build_action_buttons(labels, 0)
        plain = mid.plain
        for start, end in button_spans(labels):
            assert plain[start - 1] == "│"
            assert plain[end - 1] == "│"

    def test_pad_offsets_first_button(self):
        (start, _), *_ = button_spans(["Back"])
        assert start == len(PAD) + 1  # first button begins just after the left pad


class TestButtonRuns:
    def test_finds_each_button_run(self):
        _, _, _ = build_action_buttons(["A", "B"], 0)
        top, _, _ = build_action_buttons(["A", "B"], 0)
        runs = _button_runs(top.plain)
        assert len(runs) == 2

    def test_ignores_non_button_boxes(self):
        assert _button_runs("plain text, no boxes") == []


class TestButtonClick:
    def _panel(self, labels, height=30):
        from yeaboi.ui.shared._export_picker import _build_export_picker_screen

        return _build_export_picker_screen(mode="planning", labels=labels, selected=0, width=100, height=height)

    def test_click_maps_to_each_button(self):
        con = _console()
        labels = ["Files", "Copy", "Back"]
        panel = self._panel(labels)
        lines = con.render_lines(panel, con.options, pad=True)
        # locate the real button row + runs
        row = next(r for r, ln in enumerate(lines) if len(_button_runs("".join(s.text for s in ln))) == len(labels))
        runs = _button_runs("".join(s.text for s in lines[row]))
        for i, (start, end) in enumerate(runs):
            cx = (start + end) // 2
            for dy in (0, 1, 2):  # top / mid / bot rows all count
                assert button_click(con, panel, cx, row + 1 + dy, labels) == i

    def test_click_off_buttons_is_none(self):
        con = _console()
        labels = ["Files", "Copy", "Back"]
        panel = self._panel(labels)
        assert button_click(con, panel, 50, 3, labels) is None  # up in the title area

    def test_robust_to_popup_shift(self):
        # A warning popup pushes the button row down; hit-testing must still work.
        from yeaboi.ui.shared._export_picker import _build_export_picker_screen

        con = _console()
        actions = ["Open Setup", "Back"]
        panel = _build_export_picker_screen(
            mode="planning",
            labels=["Files", "Back"],
            selected=0,
            warning="Notion needs a token",
            warning_actions=actions,
            width=100,
            height=30,
        )
        lines = con.render_lines(panel, con.options, pad=True)
        row = next(r for r, ln in enumerate(lines) if len(_button_runs("".join(s.text for s in ln))) == len(actions))
        runs = _button_runs("".join(s.text for s in lines[row]))
        start, end = runs[0]
        assert button_click(con, panel, (start + end) // 2, row + 1, actions) == 0

    def test_empty_labels_is_none(self):
        con = _console()
        panel = self._panel(["Back"])
        assert button_click(con, panel, 10, 10, []) is None
