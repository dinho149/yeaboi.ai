"""Tests for the chat composer — buffer editing, events, and wrapping."""

from yeaboi.input_guardrails import MAX_CHAT_INPUT_CHARS
from yeaboi.ui.session.chat._composer import ChatComposer, PasteImage, Submit, Truncated, Voice


def _type(composer: ChatComposer, text: str) -> None:
    for char in text:
        composer.handle_key(char)


class TestEditing:
    def test_typing_and_submit(self):
        c = ChatComposer()
        _type(c, "hello world")
        event = c.handle_key("enter")
        assert isinstance(event, Submit)
        assert event.text == "hello world"

    def test_empty_enter_is_noop(self):
        assert ChatComposer().handle_key("enter") is None

    def test_alt_enter_splits_line(self):
        c = ChatComposer()
        _type(c, "ab")
        c.handle_key("left")
        c.handle_key("alt+enter")
        assert c.lines == ["a", "b"]
        assert (c.row, c.col) == (1, 0)

    def test_backspace_merges_lines(self):
        c = ChatComposer()
        _type(c, "a")
        c.handle_key("alt+enter")
        _type(c, "b")
        c.handle_key("left")
        c.handle_key("backspace")
        assert c.lines == ["ab"]

    def test_cursor_movement_across_lines(self):
        c = ChatComposer()
        _type(c, "ab")
        c.handle_key("alt+enter")
        _type(c, "cd")
        c.handle_key("up")
        assert c.row == 0
        c.handle_key("down")
        assert c.row == 1
        c.handle_key("left")
        c.handle_key("left")
        assert (c.row, c.col) == (1, 0)
        c.handle_key("left")
        assert (c.row, c.col) == (0, 2)
        c.handle_key("right")
        assert (c.row, c.col) == (1, 0)

    def test_word_backspace(self):
        c = ChatComposer()
        _type(c, "hello world")
        c.handle_key("word_backspace")
        assert c.text() == "hello "

    def test_clear(self):
        c = ChatComposer()
        _type(c, "abc")
        c.handle_key("clear")
        assert c.text() == ""
        assert c.is_empty()


class TestEvents:
    def test_ctrl_v_signals_paste_image(self):
        assert isinstance(ChatComposer().handle_key("ctrl+v"), PasteImage)

    def test_double_tap_space_signals_voice(self):
        c = ChatComposer()
        _type(c, "hi")
        assert c.handle_key(" ", now=100.0) is None
        event = c.handle_key(" ", now=100.2)
        assert isinstance(event, Voice)
        # The first space stays as a separator, the second is swallowed.
        assert c.text() == "hi "

    def test_slow_double_space_is_two_spaces(self):
        c = ChatComposer()
        _type(c, "hi")
        c.handle_key(" ", now=100.0)
        assert c.handle_key(" ", now=105.0) is None
        assert c.text() == "hi  "


class TestPaste:
    def test_bracketed_paste_inserts(self):
        c = ChatComposer()
        _type(c, "ab")
        c.handle_key("left")
        c.handle_key("paste:XY")
        assert c.text() == "aXYb"

    def test_insert_text_preserves_newlines(self):
        c = ChatComposer()
        assert c.insert_text("one\ntwo") is True
        assert c.lines == ["one", "two"]

    def test_paste_truncated_at_chat_cap(self):
        c = ChatComposer()
        event = c.handle_key("paste:" + "x" * (MAX_CHAT_INPUT_CHARS + 100))
        assert isinstance(event, Truncated)
        assert len(c.text()) == MAX_CHAT_INPUT_CHARS

    def test_set_text_prefill_puts_cursor_at_start(self):
        c = ChatComposer()
        c.set_text("suggested answer")
        assert (c.row, c.col) == (0, 0)
        assert c.text() == "suggested answer"


class TestVisualRows:
    def test_short_content_single_row(self):
        c = ChatComposer()
        _type(c, "hi")
        rows, cursor_idx, cursor_col = c.visual_rows(40)
        assert rows == [("hi", True)]
        assert (cursor_idx, cursor_col) == (0, 2)

    def test_long_line_wraps(self):
        c = ChatComposer()
        _type(c, "a" * 25)
        rows, cursor_idx, cursor_col = c.visual_rows(10)
        assert [r for r, _ in rows] == ["a" * 10, "a" * 10, "a" * 5]
        assert cursor_idx == 2
        assert cursor_col == 5

    def test_window_follows_cursor(self):
        c = ChatComposer()
        for i in range(10):
            _type(c, f"line{i}")
            if i < 9:
                c.handle_key("alt+enter")
        rows, cursor_idx, _ = c.visual_rows(40, max_rows=3)
        assert len(rows) == 3
        assert rows[cursor_idx][0] == "line9"


class TestCursorWord:
    def test_word_at_end_of_line(self):
        c = ChatComposer()
        _type(c, "hello /sm")
        assert c.cursor_word() == ("/sm", 6)

    def test_cursor_inside_word(self):
        c = ChatComposer()
        _type(c, "hello world")
        c.handle_key("left")
        c.handle_key("left")
        assert c.cursor_word() == ("world", 6)

    def test_after_space_is_empty(self):
        c = ChatComposer()
        _type(c, "hello ")
        assert c.cursor_word() == ("", 6)

    def test_word_on_second_row(self):
        c = ChatComposer()
        _type(c, "draft")
        c.handle_key("alt+enter")
        _type(c, "/help")
        assert c.cursor_word() == ("/help", 0)

    def test_empty_buffer(self):
        assert ChatComposer().cursor_word() == ("", 0)
