"""Render tests for the chat page builder."""

import re
from io import StringIO

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.session.chat._composer import ChatComposer
from yeaboi.ui.session.chat._screen import ChoiceRows, build_chat_screen
from yeaboi.ui.session.chat._transcript import ChatTranscript

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _console(width: int = 100, height: int = 40) -> Console:
    return Console(
        file=StringIO(), width=width, height=height, force_terminal=True, color_system="truecolor", highlight=False
    )


def _render(panel: Panel, width: int = 100) -> str:
    console = _console(width)
    console.print(panel)
    return console.file.getvalue()


def _screen(**kwargs):
    transcript = kwargs.pop("transcript", None) or ChatTranscript()
    composer = kwargs.pop("composer", None) or ChatComposer()
    defaults = dict(width=100, height=40, scroll_offset=0, console=_console())
    defaults.update(kwargs)
    return build_chat_screen(transcript, composer, {}, **defaults)


class TestLayout:
    def test_returns_page_panel(self):
        panel = _screen()
        assert isinstance(panel, Panel)
        assert panel.height == 40

    def test_transcript_and_composer_render(self):
        t = ChatTranscript()
        t.add_user("my project idea")
        t.add_assistant("great, tell me more")
        c = ChatComposer()
        c.insert_text("typing…")
        out = _render(_screen(transcript=t, composer=c))
        assert "my project idea" in out
        assert "great, tell me more" in out
        assert "typing" in out
        assert "Message" in out  # composer box title

    def test_hint_tab_attached_for_controls_drawer(self):
        panel = _screen()
        assert getattr(panel, "_hint_tab", None) is not None
        assert "Enter" in panel._hint_tab.plain

    def test_subtitle_shown(self):
        out = _render(_screen(subtitle="Q7 of 30 · Team"))
        assert "Q7 of 30" in out

    def test_progress_dots_when_stage_known(self):
        out = _render(_screen(stage="intake", subtitle="Q7 of 30"))
        assert "Describe" in out
        assert "Sprints" in out
        assert "Q7 of 30" in out

    def test_wide_terminal_centers_column(self):
        t = ChatTranscript()
        t.add_assistant("hello there")
        panel = _screen(transcript=t, width=200, console=_console(200))
        out = _ANSI.sub("", _render(panel, width=200))
        label_line = next(line for line in out.splitlines() if "▌ yeaboi" in line)
        # Centered reading column: the bubble sits ~45 columns in, not at the gutter.
        assert label_line.index("▌ yeaboi") > 20

    def test_narrow_terminal_keeps_left_gutter(self):
        t = ChatTranscript()
        t.add_assistant("hello there")
        out = _ANSI.sub("", _render(_screen(transcript=t)))
        label_line = next(line for line in out.splitlines() if "▌ yeaboi" in line)
        assert label_line.index("▌ yeaboi") < 12

    def test_tips_card_shown_until_first_user_message(self):
        assert "Getting started" in _render(_screen())
        t = ChatTranscript()
        t.add_user("my project")
        assert "Getting started" not in _render(_screen(transcript=t))

    def test_placeholder_reflects_stage(self):
        assert "Describe your project" in _render(_screen(stage="intake"))
        assert "Ask anything about the plan" in _render(_screen(stage="chat"))

    def test_greeting_choices_placeholder_offers_both_paths(self):
        # The up-front size pick must not hide that typing a description works.
        choices = ChoiceRows(options=[("Small — quick", False), ("Large — epics", False)], highlight=0, multi=False)
        out = _render(_screen(stage="intake", choices=choices))
        assert "Press 1 or 2" in out
        assert "describe your project" in out
        assert "3 for a form" not in out  # only the 3-row greeting mentions it

    def test_greeting_placeholder_mentions_form_with_three_rows(self):
        choices = ChoiceRows(
            options=[("Small — quick", False), ("Large — epics", False), ("Fill it out as a form instead", False)],
            highlight=0,
            multi=False,
        )
        out = _render(_screen(stage="intake", choices=choices))
        assert "3 for a form" in out

    def test_intake_placeholder_mentions_form_and_finish(self):
        state_with_messages = {"messages": ["m"]}
        transcript = ChatTranscript()
        panel = build_chat_screen(
            transcript,
            ChatComposer(),
            state_with_messages,
            width=100,
            height=40,
            scroll_offset=0,
            stage="intake",
            console=_console(),
        )
        out = _render(panel)
        assert "/form" in out
        assert "/finish" in out

    def test_tips_card_lists_form_and_finish(self):
        out = _render(_screen())
        assert "Getting started" in out
        assert "/form" in out
        assert "/finish" in out

    def test_scrollbar_track_always_visible(self):
        # Content fits, yet the dim rail (rgb(50,50,60)) is still drawn.
        assert "2;50;50;60" in _render(_screen())


class TestGeometry:
    def test_publish_geometry_fills_meta(self):
        t = ChatTranscript()
        for i in range(200):
            t.add_user(f"message {i}")
        meta: dict = {}
        _screen(transcript=t, scroll_meta=meta)
        assert meta["viewport_h"] > 0
        assert meta["max_offset"] > 0

    def test_scrollbar_rendered_when_overflowing(self):
        t = ChatTranscript()
        for i in range(200):
            t.add_user(f"message {i}")
        out = _render(_screen(transcript=t))
        assert "┃" in out  # scrollbar thumb, not the old ▲/▼ arrows
        assert "▲ scroll" not in out


class TestStates:
    def test_streaming_partial_visible(self):
        out = _render(_screen(stream_text="tokens arriv"))
        assert "tokens arriv" in out

    def test_processing_border_and_working_label(self):
        out = _render(_screen(processing=True, tick=0.4))
        assert "Working…" in out

    def test_notice_replaces_hint(self):
        out = _render(_screen(notice="Screenshot attached as [image #1]"))
        assert "Screenshot attached" in out

    def test_choices_render_with_highlight_and_numbers(self):
        choices = ChoiceRows(options=[("Greenfield", False), ("Existing codebase", True)], highlight=1, multi=False)
        out = _render(_screen(choices=choices))
        assert "1. Greenfield" in out
        assert "2. Existing codebase" in out
        assert "❯" in out

    def test_multi_choice_checkboxes(self):
        choices = ChoiceRows(options=[("Backend", True), ("Frontend", False)], highlight=0, multi=True)
        out = _render(_screen(choices=choices))
        assert "[x] " in out
        assert "[ ] " in out

    def test_command_menu_lists_matches(self):
        from yeaboi.ui.session.chat._commands import COMMANDS

        menu = [c for c in COMMANDS if c.name in ("export", "edit")]
        composer = ChatComposer()
        composer.insert_text("/e")
        out = _render(_screen(composer=composer, command_menu=menu))
        assert "/export" in out
        assert "/edit" in out
