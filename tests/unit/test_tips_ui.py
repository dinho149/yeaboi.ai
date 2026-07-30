"""Tests for tips rendering in the TUI: the mode-screen banner and inline hint."""

from rich.panel import Panel

from yeaboi.ui.mode_select.screens._screens import _build_mode_screen, _build_tip_rows
from yeaboi.ui.mode_select.screens._screens_secondary import _build_all_tips_screen
from yeaboi.ui.session.screens._screens_input import _image_hint, _voice_hint
from yeaboi.ui.shared import _tips
from yeaboi.voice import voice_install_command


def _tip_rows_text(**kwargs) -> str:
    """Rendered plain text of both tip rows joined, for substring assertions."""
    return "\n".join(t.plain for t in _build_tip_rows(**kwargs))


def test_voice_hint_empty_when_tips_disabled(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
    assert _voice_hint() == ""


def test_image_hint_empty_when_tips_disabled(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
    assert _image_hint() == ""


def test_image_hint_mentions_ctrl_v(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    hint = _image_hint()
    assert "Ctrl+V" in hint
    assert "screenshot" in hint


def test_image_hint_warns_off_cmd_v_on_macos(monkeypatch):
    """Mac users would reach for Cmd+V — the hint must steer them to Ctrl+V."""
    import sys

    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "not ⌘V" in _image_hint()


def test_image_hint_no_cmd_warning_on_linux(monkeypatch):
    import sys

    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert "⌘" not in _image_hint()


def test_standup_input_screen_image_hint_gated(monkeypatch):
    """The standup input screen shows the Ctrl+V hint only for image-enabled fields."""
    import io

    from rich.console import Console

    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)

    def _rendered(**kwargs) -> str:
        buf = io.StringIO()
        Console(file=buf, width=200, height=30).print(_build_standup_input_screen("Update?", "", **kwargs))
        return buf.getvalue()

    assert "Ctrl+V" in _rendered(show_image_hint=True)
    assert "Ctrl+V" not in _rendered(show_image_hint=False)


def test_voice_hint_present_when_available_and_enabled(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    hint = _voice_hint()
    assert "double-tap Space" in hint


def test_voice_hint_shows_install_when_unavailable(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (False, "x"))
    hint = _voice_hint()
    # Hint shows the install-method-aware command (not a hardcoded `uv sync`).
    assert "dictate:" in hint
    assert voice_install_command() in hint


def test_mode_screen_renders_with_tips_on(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    result = _build_mode_screen(0, width=80, height=24, shimmer_tick=0.0)
    assert isinstance(result, Panel)


def test_mode_screen_renders_with_tips_off(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
    result = _build_mode_screen(0, width=80, height=24, shimmer_tick=0.0)
    assert isinstance(result, Panel)


def test_mode_screen_renders_at_various_ticks(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    for tick in (0.0, 6.5, 42.0):
        result = _build_mode_screen(0, width=80, height=24, shimmer_tick=tick)
        assert isinstance(result, Panel)


def test_tip_rows_show_recovery_hint_when_disabled(monkeypatch):
    # Hidden tips must stay discoverable: the first row is blank (layout stays
    # stable) but the second keeps a quiet "t show tips" affordance.
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
    rows = _build_tip_rows(0.0)
    assert rows[0].plain == ""
    assert "show tips" in rows[1].plain


def test_tip_rows_show_labeled_keys(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    text = _tip_rows_text(shimmer_tick=0.0)
    assert "prev" in text and "next" in text  # browse keys, labeled
    assert "hide" in text


def test_tip_rows_have_no_position_indicator(monkeypatch):
    # No per-tip dots and no "n/total" counter — an auto-rotating tip needs no
    # position indicator, and both grew clutter as tips were added.
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    total = _tips.tip_count()
    text = _tip_rows_text(shimmer_tick=0.0, tip_offset=2)
    assert "●" not in text and "○" not in text  # no dots
    assert f"/{total}" not in text  # no counter
    _tips.get_tips.cache_clear()


def test_tip_rows_open_hint_only_for_carded_tip(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    tips = _tips.get_tips()
    carded = next(i for i, t in enumerate(tips) if t.mode_key is not None)
    ambient = next(i for i, t in enumerate(tips) if t.mode_key is None)
    assert "open" in _tip_rows_text(shimmer_tick=0.0, tip_offset=carded)
    assert "open" not in _tip_rows_text(shimmer_tick=0.0, tip_offset=ambient)
    _tips.get_tips.cache_clear()


def test_tip_rows_new_badge_when_flagged(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    tips = _tips.get_tips()
    new_idx = next(i for i, t in enumerate(tips) if t.is_new)
    plain_idx = next(i for i, t in enumerate(tips) if not t.is_new)
    assert "NEW" in _tip_rows_text(shimmer_tick=0.0, tip_offset=new_idx)
    assert "NEW" not in _tip_rows_text(shimmer_tick=0.0, tip_offset=plain_idx)
    _tips.get_tips.cache_clear()


def test_tip_offset_shifts_the_shown_tip(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    tips = _tips.get_tips()
    # At tick 0 the auto index is 0, so a browse offset selects tips[offset] —
    # and because it's an offset (not a pin) auto-rotation keeps advancing.
    assert tips[0].text in _tip_rows_text(shimmer_tick=0.0, tip_offset=0)
    assert tips[2].text in _tip_rows_text(shimmer_tick=0.0, tip_offset=2)
    _tips.get_tips.cache_clear()


# --- All Tips gallery page (opened with `a`) ---------------------------------


def _all_tips_rendered(**kwargs) -> str:
    import io

    from rich.console import Console

    buf = io.StringIO()
    Console(file=buf, width=100, height=30).print(_build_all_tips_screen(**kwargs))
    return buf.getvalue()


def test_all_tips_screen_renders_panel(monkeypatch):
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    # No actions of its own any more — the gallery is read-only and going back is
    # the app-wide back tab (the "Copy all" button was dropped with the button row).
    result = _build_all_tips_screen(shimmer_tick=0.0, sub_reveal=99)
    assert isinstance(result, Panel)
    _tips.get_tips.cache_clear()


def test_all_tips_screen_shows_a_tip_and_new_badge(monkeypatch):
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    out = _all_tips_rendered(shimmer_tick=0.0, sub_reveal=99)
    assert "NEW" in out
    assert "opens" in out  # a carded tip's "→ opens <Mode>" note
    _tips.get_tips.cache_clear()


def test_all_tips_screen_groups_every_tip_once(monkeypatch):
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    monkeypatch.setattr(
        "yeaboi.ui.mode_select.screens._screens_secondary.build_scrollbar",
        lambda *_args, **_kwargs: None,
    )
    _tips.get_tips.cache_clear()
    tips = _tips.get_tips()
    out = _all_tips_rendered(height=200, shimmer_tick=0.0, sub_reveal=99)
    assert out.index("Modes") < out.index("More workflows") < out.index("Shortcuts & setup")
    content_lines = [line.strip().strip("│").strip() for line in out.splitlines()[1:-1]]
    normalized_out = " ".join(" ".join(content_lines).split())
    for tip in tips:
        _prefix, marker, display_text = tip.text.partition("Tip: ")
        expected = display_text if marker else tip.text
        assert normalized_out.count(" ".join(expected.split())) == 1
    _tips.get_tips.cache_clear()


def test_all_tips_screen_omits_terminal_unsafe_emoji_prefixes(monkeypatch):
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    out = _all_tips_rendered(height=200, shimmer_tick=0.0, sub_reveal=99)
    assert "Analysis reads your board" in out
    assert "🔍" not in out
    assert "🗺️" not in out
    assert "Tip:" not in out
    _tips.get_tips.cache_clear()


def test_all_tips_screen_keeps_full_frame_at_common_widths(monkeypatch):
    import io

    from rich.cells import cell_len
    from rich.console import Console

    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    for width, height in ((60, 20), (80, 24), (100, 30)):
        buf = io.StringIO()
        console = Console(file=buf, width=width, height=height, color_system=None)
        console.print(
            _build_all_tips_screen(
                width=width,
                height=height,
                shimmer_tick=0.0,
                sub_reveal=99,
            )
        )
        lines = buf.getvalue().splitlines()
        assert len(lines) == height
        assert lines[0].startswith("╭") and lines[0].endswith("╮")
        assert lines[-1].startswith("╰") and lines[-1].endswith("╯")
        assert all(cell_len(line) == width for line in lines)
        assert all(line.startswith("│") and line.endswith("│") for line in lines[1:-1])
    _tips.get_tips.cache_clear()


def test_all_tips_screen_shows_status_message(monkeypatch):
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    out = _all_tips_rendered(shimmer_tick=0.0, sub_reveal=99, message="Copied to clipboard")
    assert "Copied to clipboard" in out
    _tips.get_tips.cache_clear()


def test_all_tips_screen_scrolls(monkeypatch):
    # A large scroll offset is clamped and still renders a Panel (no crash).
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    _tips.get_tips.cache_clear()
    result = _build_all_tips_screen(scroll_offset=999, shimmer_tick=1.0, sub_reveal=99)
    assert isinstance(result, Panel)
    _tips.get_tips.cache_clear()


class TestFeedbackComposeBubble:
    """The duck's feedback composer — the `f` shortcut's speech bubble.

    Three fields (Type / Area / message) in a bubble that replaces the tip in the
    companion lane, so feedback is given to the duck rather than on a page of its
    own. Wrapping happens here, at build time, because the box is a fixed width.
    """

    def _state(self, **over):
        base = {"field": 2, "kind": 0, "area": 0, "buf": "", "cur": 0, "status": ""}
        return {**base, **over}

    def _render(self, state, *, width=140, height=44):
        import io

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens import _build_mode_screen

        buf = io.StringIO()
        Console(file=buf, width=width, height=height, legacy_windows=False).print(
            _build_mode_screen(0, width=width, height=height, shimmer_tick=1.0, desc_reveal=999, compose=state)
        )
        return buf.getvalue()

    def test_bubble_replaces_the_tip_and_shows_both_selectors(self):
        out = self._render(self._state())
        assert "Tell the duck" in out
        assert "Type" in out and "Area" in out
        assert "‹ Bug ›" in out and "‹ general ›" in out  # the current choices, bracketed
        assert "What's on your mind?" in out  # placeholder while empty

    def test_selected_values_follow_the_state(self):
        from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES

        out = self._render(self._state(kind=FEEDBACK_TYPES.index("Feature"), area=FEEDBACK_AREAS.index("retro")))
        assert "‹ Feature ›" in out and "‹ retro ›" in out

    def test_status_replaces_the_hint_while_sending(self):
        out = self._render(self._state(buf="x", cur=1, status="sending…"))
        assert "sending…" in out
        assert "Esc cancel" not in out  # the keys don't apply mid-flight

    def test_composer_takes_a_wider_lane_than_the_tip(self):
        from yeaboi.ui.mode_select.screens._screens import _COMPANION_COLS, _COMPOSE_COLS, _compose_lane_cols

        assert _COMPOSE_COLS > _COMPANION_COLS
        assert _compose_lane_cols(140) == _COMPOSE_COLS
        # Too narrow to spare the columns → fall back to the tip lane rather than
        # squeezing the mode list.
        assert _compose_lane_cols(60) == _COMPANION_COLS


class TestComposeWrap:
    """_wrap_with_offsets — wrapping that can map a cursor index back to a row."""

    def _wrap(self, text, width=20):
        from yeaboi.ui.mode_select.screens._screens import _wrap_with_offsets

        return _wrap_with_offsets(text, width)

    def test_short_text_is_one_line(self):
        assert self._wrap("hello there") == [("hello there", 0)]

    def test_offsets_point_at_the_original_string(self):
        text = "the retro board loses cards when two people type at once"
        for line, off in self._wrap(text):
            assert text[off : off + len(line)] == line  # the offset really indexes the source

    def test_an_overlong_word_is_hard_broken(self):
        # A pasted URL or a keysmash has no spaces to break on; it must still wrap
        # rather than run off the edge of a fixed-width box.
        out = self._wrap("w" * 55, width=20)
        assert len(out) == 3
        assert all(len(line) <= 20 for line, _ in out)
        assert "".join(line for line, _ in out) == "w" * 55

    def test_every_line_fits_the_width(self):
        text = "feedback from the duck should wrap neatly at the box edge every single time"
        assert all(len(line) <= 20 for line, _ in self._wrap(text))


class TestFeedbackComposeKeys:
    """_feedback_compose_key — the bubble's three-field state machine."""

    def _state(self, **over):
        base = {"field": 2, "kind": 0, "area": 0, "buf": "", "cur": 0, "status": "", "thread": None, "done_at": 0.0}
        return {**base, **over}

    def _press(self, key, state):
        from yeaboi.ui import mode_select

        return mode_select._feedback_compose_key(key, state)

    def test_up_down_walk_the_fields(self):
        st = self._press("up", self._state(field=2))
        assert st["field"] == 1  # message -> area
        assert self._press("up", st)["field"] == 0  # -> type
        assert self._press("up", self._state(field=0))["field"] == 2  # wraps back round

    def test_left_right_cycle_the_focused_selector(self):
        from yeaboi.feedback import FEEDBACK_TYPES

        st = self._press("right", self._state(field=0))
        assert st["kind"] == 1
        assert self._press("left", self._state(field=0, kind=0))["kind"] == len(FEEDBACK_TYPES) - 1  # wraps

    def test_typing_only_reaches_the_message_field(self):
        # On a selector, a stray letter must not leak into the message buffer.
        assert self._press("x", self._state(field=0))["buf"] == ""
        assert self._press("x", self._state(field=2))["buf"] == "x"

    def test_esc_closes_and_keeps_the_back_tab_up(self, monkeypatch):
        # Esc is armed by the input chokepoint before the screen sees it, so a
        # screen that KEEPS the key has to un-arm the tab's fold-away.
        from yeaboi.ui.shared import _music_bar

        cancelled: list = []
        monkeypatch.setattr(_music_bar, "cancel_back_retract", lambda: cancelled.append(True))
        assert self._press("esc", self._state(buf="half a thought", cur=5)) is None
        assert cancelled

    def test_enter_on_an_empty_message_just_closes(self):
        assert self._press("enter", self._state()) is None

    def test_enter_sends_the_chosen_type_and_area(self, monkeypatch):
        from yeaboi import feedback

        sent: list = []
        monkeypatch.setattr(
            feedback, "submit_feedback", lambda *a, **k: sent.append(a) or feedback.FeedbackResult(ok=True)
        )
        st = self._press("enter", self._state(field=2, kind=1, area=4, buf="cards vanish\nsometimes", cur=0))
        st["thread"].join(timeout=5)
        kind, area, title, body, _images = sent[0]
        assert (kind, area) == (feedback.FEEDBACK_TYPES[1], feedback.FEEDBACK_AREAS[4])
        assert title == "cards vanish"  # the opening line, so the issue is scannable
        assert body == "cards vanish\nsometimes"

    def test_keys_are_ignored_while_in_flight(self):
        st = self._state(buf="x", cur=1, status="sending…", thread=object())
        assert self._press("y", st)["buf"] == "x"


class TestComposeSelectorWindow:
    """The selectors are a FIXED list — the marker moves, the words don't rotate."""

    AREAS = ("general", "analysis", "planning", "standup", "retro", "performance", "reporting", "usage", "settings")

    def _window(self, idx, width=48):
        from yeaboi.ui.mode_select.screens._screens import _compose_window

        return self._render(idx, width), _compose_window(self.AREAS, idx, width)

    def _render(self, idx, width=48):
        from yeaboi.ui.mode_select.screens._screens import _compose_chips

        return _compose_chips(self.AREAS, idx, True, width).plain

    def test_everything_fits_when_there_is_room(self):
        from yeaboi.ui.mode_select.screens._screens import _compose_window

        assert _compose_window(("Bug", "Feature", "Improvement", "Other"), 0, 60) == (0, 4)

    def test_the_words_hold_still_while_the_marker_moves(self):
        # Stepping through the middle of the list must not scroll it: the same
        # options stay visible, only the brackets move.
        from yeaboi.ui.mode_select.screens._screens import _compose_window

        assert _compose_window(self.AREAS, 0, 48) == _compose_window(self.AREAS, 2, 48)
        assert "‹ general ›" in self._render(0)
        assert "‹ planning ›" in self._render(2)

    def test_the_window_slides_only_at_the_edges(self):
        _text, (start, end) = self._window(0)
        assert start == 0
        _text, (start_end, _e) = self._window(len(self.AREAS) - 1)
        assert start_end > 0  # had to slide to reach the last option

    def test_the_selection_is_always_visible(self):
        for idx, name in enumerate(self.AREAS):
            assert f"‹ {name} ›" in self._render(idx)

    def test_overflow_is_marked_without_looking_like_a_bracket(self):
        row = self._render(len(self.AREAS) - 1)
        assert row.startswith("… ")  # not "‹ ", which reads as a selection marker


class TestComposeRichAffordances:
    """Ctrl+V screenshot paste and double-tap-Space dictation, kept from the full form."""

    def _state(self, **over):
        from yeaboi.ui.shared._voice_input import DoubleTapSpace

        base = {
            "field": 2,
            "kind": 0,
            "area": 0,
            "buf": "",
            "cur": 0,
            "status": "",
            "notice": "",
            "attachments": [],
            "dts": DoubleTapSpace(),
            "thread": None,
            "done_at": 0.0,
        }
        return {**base, **over}

    def test_ctrl_v_inserts_a_chip_at_the_cursor(self, monkeypatch):
        from yeaboi.ui.shared import _attachments

        monkeypatch.setattr(_attachments, "handle_ctrl_v", lambda *a, **k: "[image #1]")
        from yeaboi.ui import mode_select

        st = self._state(buf="before after", cur=7)
        out = mode_select._feedback_compose_key("ctrl+v", st, render=lambda update=True: None)
        assert out["buf"] == "before [image #1]after"
        assert "image #1" in out["notice"]

    def test_ctrl_v_without_a_render_hook_is_ignored(self):
        # The hook is how the paste takes over the screen; no hook, no paste.
        from yeaboi.ui import mode_select

        assert mode_select._feedback_compose_key("ctrl+v", self._state(buf="x", cur=1))["buf"] == "x"

    def test_double_tap_space_dictates_into_the_message(self, monkeypatch):
        from yeaboi.ui.shared import _voice_input

        monkeypatch.setattr(_voice_input, "record_voice_input", lambda *a, **k: "spoken words")
        import time as _t

        from yeaboi.ui import mode_select

        st = self._state(buf="note ", cur=5)  # the first tap's space is already in
        st["dts"].is_double(False, _t.monotonic())  # arm it, as the first Space press would
        out = mode_select._feedback_compose_key(
            " ", st, console=object(), live=object(), read_key=lambda **k: "", render=lambda update=True: None
        )
        assert out["buf"] == "note spoken words"

    def test_a_single_space_is_just_a_space(self, monkeypatch):
        from yeaboi.ui import mode_select

        out = mode_select._feedback_compose_key(
            " ", self._state(buf="hi", cur=2), console=object(), live=object(), read_key=lambda **k: ""
        )
        assert out["buf"] == "hi "

    def test_attachments_ride_along_on_submit(self, monkeypatch):
        from yeaboi import feedback

        sent: list = []
        monkeypatch.setattr(feedback, "submit_feedback", lambda *a: sent.append(a) or feedback.FeedbackResult(ok=True))
        monkeypatch.setattr("yeaboi.ui.shared._attachments.referenced_images", lambda _t, a: list(a))
        from yeaboi.ui import mode_select

        st = self._state(buf="see [image #1]", cur=0, attachments=["/tmp/a.png"])
        mode_select._feedback_compose_key("enter", st)["thread"].join(timeout=5)
        assert sent[0][4] == ["/tmp/a.png"]
