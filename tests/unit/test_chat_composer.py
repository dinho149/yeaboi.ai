"""Tests for the chat composer — buffer editing, events, and wrapping."""

from yeaboi.input_guardrails import MAX_CHAT_INPUT_CHARS
from yeaboi.ui.session.chat._composer import (
    ChatComposer,
    Cleared,
    InsertResult,
    PasteImage,
    Restored,
    Submit,
    Truncated,
    Voice,
    clear_notice,
    paste_notice,
)


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

    def test_reset_leaves_attachments_alone(self):
        # Regression guard: _input_loop resets on submit and the caller reads
        # attachments AFTERWARDS to resolve image chips.
        c = ChatComposer()
        _type(c, "look [image #1]")
        c.attachments = ["/tmp/shot.png"]
        c.reset()
        assert c.attachments == ["/tmp/shot.png"]


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
        assert c.insert_text("one\ntwo").ok
        assert c.lines == ["one", "two"]

    def test_paste_truncated_at_chat_cap(self):
        c = ChatComposer()
        event = c.handle_key("paste:" + "x" * (MAX_CHAT_INPUT_CHARS + 100))
        assert isinstance(event, Truncated)
        assert len(c.text()) == MAX_CHAT_INPUT_CHARS

    def test_insert_text_reports_offered_kept_dropped(self):
        c = ChatComposer()
        result = c.insert_text("x" * (MAX_CHAT_INPUT_CHARS + 12))
        assert (result.offered, result.kept, result.dropped) == (
            MAX_CHAT_INPUT_CHARS + 12,
            MAX_CHAT_INPUT_CHARS,
            12,
        )
        assert not result.ok

    def test_insert_text_counts_reader_drops_into_offered(self):
        # The reader capped the paste before the composer ever saw it; those
        # characters still belong in "what you pasted".
        c = ChatComposer()
        result = c.insert_text("x" * 100, already_dropped=24_000)
        assert result.offered == 24_100
        assert result.kept == 100
        assert result.dropped == 24_000

    def test_insert_text_of_empty_string_is_ok(self):
        result = ChatComposer().insert_text("")
        assert (result.offered, result.kept, result.dropped) == (0, 0, 0)
        assert result.ok

    def test_paste_into_a_full_box_keeps_nothing(self):
        c = ChatComposer()
        c.insert_text("x" * MAX_CHAT_INPUT_CHARS)
        result = c.insert_text("more")
        assert result.kept == 0
        assert result.dropped == 4

    def test_truncated_event_carries_the_numbers(self):
        c = ChatComposer()
        event = c.handle_key("paste:" + "x" * (MAX_CHAT_INPUT_CHARS + 100), dropped=5_000)
        assert isinstance(event, Truncated)
        assert event.offered == MAX_CHAT_INPUT_CHARS + 5_100
        assert event.kept == MAX_CHAT_INPUT_CHARS
        assert event.dropped == 5_100

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


class TestClearUndo:
    def test_clear_stashes_text_and_chips(self):
        c = ChatComposer()
        _type(c, "abc")
        c.attachments = ["/tmp/a.png", "/tmp/b.png"]
        event = c.handle_key("clear")
        assert isinstance(event, Cleared)
        assert (event.chars, event.images) == (3, 2)
        assert c.text() == ""
        assert c.attachments == []

    def test_second_clear_restores_text_cursor_and_chips(self):
        c = ChatComposer()
        _type(c, "hello")
        c.handle_key("left")
        c.attachments = ["/tmp/a.png"]
        c.handle_key("clear")
        event = c.handle_key("clear")
        assert isinstance(event, Restored)
        assert (event.chars, event.images) == (5, 1)
        assert c.text() == "hello"
        assert (c.row, c.col) == (0, 4)
        assert c.attachments == ["/tmp/a.png"]

    def test_clear_on_empty_box_with_no_stash_does_nothing(self):
        c = ChatComposer()
        assert c.handle_key("clear") is None
        assert c.text() == ""

    def test_whitespace_only_box_is_clearable(self):
        # has_content(), not is_empty(): three spaces are something to destroy.
        c = ChatComposer()
        c.insert_text("   ")  # not _type: fast repeated spaces are the voice gesture
        assert isinstance(c.handle_key("clear"), Cleared)
        assert isinstance(c.handle_key("clear"), Restored)
        assert c.text() == "   "

    def test_attachments_alone_count_as_content(self):
        c = ChatComposer()
        c.attachments = ["/tmp/a.png"]
        event = c.handle_key("clear")
        assert isinstance(event, Cleared)
        assert (event.chars, event.images) == (0, 1)

    def test_undo_is_single_level(self):
        c = ChatComposer()
        _type(c, "first")
        c.handle_key("clear")
        c.handle_key("clear")
        c.handle_key("clear")
        _type(c, "second")
        c.handle_key("clear")
        c.handle_key("clear")
        assert c.text() == "second"

    def test_submit_burns_the_stash(self):
        c = ChatComposer()
        _type(c, "abc")
        c.handle_key("clear")
        _type(c, "sent")
        assert isinstance(c.handle_key("enter"), Submit)
        c.reset()
        assert c.handle_key("clear") is None
        assert not c.has_stash()

    def test_has_stash_reports_recoverability(self):
        c = ChatComposer()
        assert not c.has_stash()
        _type(c, "abc")
        c.handle_key("clear")
        assert c.has_stash()
        c.handle_key("clear")
        assert not c.has_stash()

    def test_stash_is_immune_to_later_edits(self):
        c = ChatComposer()
        _type(c, "abc")
        c.handle_key("clear")
        _type(c, "zzz")
        c.attachments.append("/tmp/late.png")
        c.reset()
        c.attachments = []
        assert isinstance(c.handle_key("clear"), Restored)
        assert c.text() == "abc"


class TestNotices:
    def test_clear_notice_counts_characters(self):
        assert clear_notice(Cleared(chars=1240, images=0)) == (
            "Cleared the message (1,240 characters) — Ctrl+U again to undo."
        )

    def test_clear_notice_pluralises_images(self):
        assert "1 image)" in clear_notice(Cleared(chars=5, images=1))
        assert "2 images)" in clear_notice(Cleared(chars=5, images=2))

    def test_restore_notice(self):
        assert clear_notice(Restored(chars=1240, images=0)) == "Restored your message (1,240 characters)."

    def test_paste_notice_quotes_all_three_numbers(self):
        notice = paste_notice(InsertResult(offered=34_812, kept=10_000, dropped=24_812))
        assert "Pasted 34,812 characters" in notice
        assert "kept 10,000" in notice
        assert "dropped 24,812" in notice

    def test_paste_notice_when_nothing_fits(self):
        notice = paste_notice(InsertResult(offered=500, kept=0, dropped=500))
        assert notice.startswith("Nothing pasted")
        assert f"{MAX_CHAT_INPUT_CHARS:,}-character" in notice
