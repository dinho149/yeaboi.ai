"""Chat composer — the multiline input buffer at the bottom of the chat screen.

Pure editing state + key handling, extracted from the description-input
editor (_phases_intake._phase_description_input) so it can be unit-tested
without a terminal. Screen-owning concerns (voice recording, clipboard image
reads) stay in the driver: the composer only *signals* them via
ComposerEvent, because they need live/console/render closures.

Image chips ([image #N]) are plain printable text, so cursor movement,
backspace and word ops work on them with zero special cases — the whole
point of the chip design in ui/shared/_attachments.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from yeaboi.input_guardrails import MAX_CHAT_INPUT_CHARS
from yeaboi.ui.shared._voice_input import DoubleTapSpace


@dataclass(frozen=True)
class Submit:
    """Enter pressed with content: the trimmed text (images resolved by the driver)."""

    text: str


@dataclass(frozen=True)
class Voice:
    """Double-tap Space — the driver runs record_voice_input and inserts the result."""


@dataclass(frozen=True)
class PasteImage:
    """Ctrl+V — the driver reads the clipboard image and inserts the chip."""


@dataclass(frozen=True)
class Truncated:
    """A paste was cut at MAX_CHAT_INPUT_CHARS — the driver shows a notice."""


ComposerEvent = Submit | Voice | PasteImage | Truncated


@dataclass
class ChatComposer:
    """Multiline editing buffer with a row/col cursor."""

    lines: list[str] = field(default_factory=lambda: [""])
    row: int = 0
    col: int = 0
    attachments: list[str] = field(default_factory=list)
    _dts: DoubleTapSpace = field(default_factory=DoubleTapSpace)

    # -- content -----------------------------------------------------------

    def text(self) -> str:
        return "\n".join(self.lines)

    def is_empty(self) -> bool:
        return not self.text().strip()

    def reset(self) -> None:
        self.lines = [""]
        self.row = 0
        self.col = 0

    def set_text(self, text: str) -> None:
        """Replace the buffer (suggestion prefill), cursor at the start."""
        self.lines = text.split("\n") or [""]
        self.row = 0
        self.col = 0

    def cursor_word(self) -> tuple[str, int]:
        """The space-delimited token at (or immediately before) the cursor.

        Returns (word, start_col) on the current row; ("", col) when the
        cursor touches only whitespace. The /-menu and Tab completion key off
        this token rather than the whole buffer, so slash commands stay
        reachable after the user has started typing a message.
        """
        line = self.lines[self.row]
        start = self.col
        while start > 0 and line[start - 1] != " ":
            start -= 1
        end = self.col
        while end < len(line) and line[end] != " ":
            end += 1
        return line[start:end], start

    def insert_text(self, text: str) -> bool:
        """Insert text at the cursor (paste, voice, chips), splitting on newlines.

        Returns False when the insertion was truncated at MAX_CHAT_INPUT_CHARS
        — the same constant submit-time validation uses, so truncation and
        validation can never disagree.
        """
        budget = MAX_CHAT_INPUT_CHARS - len(self.text())
        truncated = len(text) > budget
        if truncated:
            text = text[: max(0, budget)]
        if not text:
            return not truncated

        parts = text.split("\n")
        line = self.lines[self.row]
        tail = line[self.col :]
        self.lines[self.row] = line[: self.col] + parts[0]
        self.col = len(self.lines[self.row])
        for part in parts[1:]:
            self.row += 1
            self.lines.insert(self.row, part)
            self.col = len(part)
        self.lines[self.row] += tail
        return not truncated

    # -- key handling ------------------------------------------------------

    def handle_key(self, key: str, *, now: float | None = None) -> ComposerEvent | None:
        """Apply one key to the buffer. Returns an event for the driver, or None.

        Only buffer-editing keys are handled here; the driver routes scroll
        keys, choice navigation, and Esc before calling this.
        """
        if key == "enter":
            text = self.text().strip()
            if text:
                return Submit(text)
            return None
        if key == "alt+enter":
            line = self.lines[self.row]
            self.lines[self.row] = line[: self.col]
            self.lines.insert(self.row + 1, line[self.col :])
            self.row += 1
            self.col = 0
        elif key == "backspace":
            if self.col > 0:
                line = self.lines[self.row]
                self.lines[self.row] = line[: self.col - 1] + line[self.col :]
                self.col -= 1
            elif self.row > 0:
                prev_len = len(self.lines[self.row - 1])
                self.lines[self.row - 1] += self.lines[self.row]
                self.lines.pop(self.row)
                self.row -= 1
                self.col = prev_len
        elif key == "clear":
            self.reset()
        elif key == "up":
            if self.row > 0:
                self.row -= 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif key == "down":
            if self.row < len(self.lines) - 1:
                self.row += 1
                self.col = min(self.col, len(self.lines[self.row]))
        elif key == "left":
            if self.col > 0:
                self.col -= 1
            elif self.row > 0:
                self.row -= 1
                self.col = len(self.lines[self.row])
        elif key == "right":
            if self.col < len(self.lines[self.row]):
                self.col += 1
            elif self.row < len(self.lines) - 1:
                self.row += 1
                self.col = 0
        elif key == "shift+left":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            self.col = _word_boundary_left(self.lines[self.row], self.col)
        elif key == "shift+right":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_right

            self.col = _word_boundary_right(self.lines[self.row], self.col)
        elif key == "word_backspace":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            line = self.lines[self.row]
            word_start = _word_boundary_left(line, self.col)
            self.lines[self.row] = line[:word_start] + line[self.col :]
            self.col = word_start
        elif key == "ctrl+v":
            return PasteImage()
        elif key.startswith("paste:"):
            # Bracketed paste (newlines already stripped by read_key; /paste is
            # the newline-preserving alternative via read_clipboard_text).
            if not self.insert_text(key[6:]):
                return Truncated()
        elif len(key) == 1 and key.isprintable():
            line = self.lines[self.row]
            prev_is_space = self.col > 0 and line[self.col - 1] == " "
            if key == " " and self._dts.is_double(prev_is_space, now if now is not None else time.monotonic()):
                # Double-tap Space → dictate; the first space stays as a
                # separator, the second is swallowed by the gesture.
                return Voice()
            self.lines[self.row] = line[: self.col] + key + line[self.col :]
            self.col += 1
        return None

    # -- rendering support -------------------------------------------------

    def visual_rows(self, wrap_w: int, max_rows: int = 6) -> tuple[list[tuple[str, bool]], int, int]:
        """Wrapped buffer rows for the input panel, following the cursor.

        Returns (rows, cursor_row_index, cursor_col) where rows is a list of
        (text, is_cursor_row) limited to max_rows around the cursor.
        """
        wrap_w = max(10, wrap_w)
        visual: list[tuple[str, int, int]] = []  # (chunk, source_row, chunk_start)
        for i, line in enumerate(self.lines):
            if not line:
                visual.append(("", i, 0))
                continue
            for start in range(0, len(line), wrap_w):
                visual.append((line[start : start + wrap_w], i, start))

        cursor_idx = 0
        cursor_col = 0
        for idx, (chunk, source_row, chunk_start) in enumerate(visual):
            if source_row == self.row and chunk_start <= self.col <= chunk_start + len(chunk):
                cursor_idx = idx
                cursor_col = self.col - chunk_start
                # Keep scanning: the cursor at a wrap boundary belongs to the
                # later chunk's start, which a following iteration claims.

        if len(visual) <= max_rows:
            window = visual
            cursor_window_idx = cursor_idx
        else:
            top = min(max(0, cursor_idx - max_rows + 1), len(visual) - max_rows)
            window = visual[top : top + max_rows]
            cursor_window_idx = cursor_idx - top

        rows = [(chunk, idx == cursor_window_idx) for idx, (chunk, _r, _s) in enumerate(window)]
        return rows, cursor_window_idx, cursor_col
