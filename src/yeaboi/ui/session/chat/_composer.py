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
class InsertResult:
    """What an insertion actually managed to do.

    ``offered`` counts what the user tried to insert, INCLUDING anything the
    reader already dropped at its own paste ceiling — so a notice can quote what
    was pasted rather than only what survived the terminal. ``kept`` is what
    reached the buffer.
    """

    offered: int
    kept: int
    dropped: int

    @property
    def ok(self) -> bool:
        return self.dropped == 0


@dataclass(frozen=True)
class Truncated(InsertResult):
    """An insertion hit MAX_CHAT_INPUT_CHARS — the driver shows a loud notice.

    Subclasses InsertResult (adding no fields) so one formatter — paste_notice —
    serves both the bracketed-paste event and /paste's return value.
    """


@dataclass(frozen=True)
class Cleared:
    """Ctrl+U on a box with content: buffer and chips stashed, box emptied."""

    chars: int
    images: int


@dataclass(frozen=True)
class Restored:
    """Ctrl+U on an empty box holding a stash: the previous draft is back."""

    chars: int
    images: int


ComposerEvent = Submit | Voice | PasteImage | Truncated | Cleared | Restored

NEWLINE_KEY = "Ctrl+N"
"""The newline key we advertise.

Alt+Enter and Shift+Enter also arrive as "alt+enter" (see ui/shared/_input.py),
but neither reaches the app on a default iTerm2 or Terminal.app profile —
Alt+Enter needs Option-as-Meta and Shift+Enter needs CSI-u modifier reporting.
Ctrl+N works in every terminal, so it is the only one we promise; the hint used
to name Alt+Enter, which is precisely the one most users cannot press.
"""


@dataclass(frozen=True)
class _Stash:
    """A cleared draft, held for one Ctrl+U undo. Immutable so a later edit of
    the live buffer cannot reach back into it."""

    lines: tuple[str, ...]
    row: int
    col: int
    attachments: tuple[str, ...]


def clear_notice(event: Cleared | Restored) -> str:
    """The hint-line notice for a Ctrl+U clear or its undo."""
    images = ""
    if event.images:
        images = f", {event.images} image{'' if event.images == 1 else 's'}"
    if isinstance(event, Cleared):
        return f"Cleared the message ({event.chars:,} characters{images}) — Ctrl+U again to undo."
    return f"Restored your message ({event.chars:,} characters{images})."


def paste_notice(result: InsertResult) -> str:
    """The loud notice for a paste that did not fit.

    Numbers first: the hint row clips at roughly 70 cells on an 80-column
    terminal, and the counts are the part that has to survive the cut.
    """
    if result.kept == 0:
        return (
            f"Nothing pasted — the message is already at the {MAX_CHAT_INPUT_CHARS:,}-character "
            "limit. Shorten it, then paste again."
        )
    return (
        f"Pasted {result.offered:,} characters — kept {result.kept:,}, dropped {result.dropped:,}. "
        f"The message limit is {MAX_CHAT_INPUT_CHARS:,} characters."
    )


@dataclass
class ChatComposer:
    """Multiline editing buffer with a row/col cursor."""

    lines: list[str] = field(default_factory=lambda: [""])
    row: int = 0
    col: int = 0
    attachments: list[str] = field(default_factory=list)
    _dts: DoubleTapSpace = field(default_factory=DoubleTapSpace)
    _stash: _Stash | None = None

    # -- content -----------------------------------------------------------

    def text(self) -> str:
        return "\n".join(self.lines)

    def is_empty(self) -> bool:
        return not self.text().strip()

    def reset(self) -> None:
        """Empty the buffer, leaving ``attachments`` alone.

        Deliberate: _input_loop calls this the instant a message is submitted,
        and the caller reads composer.attachments AFTERWARDS to resolve image
        chips. Clearing them here would silently detach every image on send.
        Ctrl+U clears both — see clear_with_stash.
        """
        self.lines = [""]
        self.row = 0
        self.col = 0

    def has_content(self) -> bool:
        """Anything Ctrl+U would throw away: any character at all, or any chip.

        Deliberately not is_empty(), which strips — that one answers "does this
        look empty to the user" (ghost placeholder, choice navigation). Ctrl+U
        asks "is there something to destroy", and a box holding three spaces has
        something to destroy.
        """
        return bool(self.text()) or bool(self.attachments)

    def has_stash(self) -> bool:
        """True while a cleared draft is still recoverable."""
        return self._stash is not None

    def forget_stash(self) -> None:
        """Drop the undo history — the draft it belonged to has been sent.

        handle_key's Submit branch does this for a typed message, but the input
        loop also returns from a choices answer and from an inline /command,
        and neither goes through handle_key. A stash surviving the turn both
        blocks every later suggestion prefill (the guard tests has_stash) and
        lets a Ctrl+U three questions later resurrect a draft from this one.
        """
        self._stash = None

    def clear_with_stash(self) -> Cleared | Restored | None:
        """Ctrl+U: empty the box, stashing it — or restore the last stash.

        Single level on purpose: a second Ctrl+U means "give it back", not "go
        further back". Returns None on an empty box with nothing stashed, so the
        driver shows no notice for a keypress that did nothing.
        """
        if self.has_content():
            self._stash = _Stash(tuple(self.lines), self.row, self.col, tuple(self.attachments))
            event = Cleared(chars=len(self.text()), images=len(self.attachments))
            self.reset()
            self.attachments = []
            return event
        if self._stash is None:
            return None
        stash, self._stash = self._stash, None
        self.lines = list(stash.lines)
        self.row, self.col = stash.row, stash.col
        self.attachments = list(stash.attachments)
        return Restored(chars=len(self.text()), images=len(self.attachments))

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

    def insert_text(self, text: str, *, already_dropped: int = 0) -> InsertResult:
        """Insert text at the cursor (paste, voice, chips), splitting on newlines.

        Truncates at MAX_CHAT_INPUT_CHARS — the same constant submit-time
        validation uses, so truncation and validation can never disagree — and
        reports what that cost. ``already_dropped`` counts characters the reader
        threw away above its own paste ceiling before handing the text over: the
        composer never sees them, but they belong in ``offered`` so the notice
        can name what the user actually pasted.
        """
        offered = len(text) + already_dropped
        budget = max(0, MAX_CHAT_INPUT_CHARS - len(self.text()))
        text = text[:budget]
        result = InsertResult(offered=offered, kept=len(text), dropped=offered - len(text))
        if not text:
            return result

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
        return result

    # -- key handling ------------------------------------------------------

    def handle_key(self, key: str, *, now: float | None = None, dropped: int = 0) -> ComposerEvent | None:
        """Apply one key to the buffer. Returns an event for the driver, or None.

        Only buffer-editing keys are handled here; the driver routes scroll
        keys, choice navigation, and Esc before calling this.

        ``dropped`` is the reader's paste overflow (ui.shared.take_paste_dropped),
        passed in rather than read here so the composer stays terminal-free.
        """
        if key == "enter":
            text = self.text().strip()
            if text:
                # A sent message is not undoable: resurrecting it on a later
                # Ctrl+U would read as a bug, not as an undo.
                self._stash = None
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
            return self.clear_with_stash()
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
            # Taken whole: this is the multiline case of ui.shared.paste_payload
            # (insert_text splits on "\n"), so no reshaping is needed and the
            # composer stays free of any terminal import.
            result = self.insert_text(key[len("paste:") :], already_dropped=dropped)
            if not result.ok:
                return Truncated(offered=result.offered, kept=result.kept, dropped=result.dropped)
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
