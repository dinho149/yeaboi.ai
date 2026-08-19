"""Tests for yeaboi.claude_auth (subscription sign-in via the Claude Code CLI).

The parsers are exercised against output shaped like the real CLI's — cursor-motion
escapes instead of spaces, a spinner, and the authorize URL — because that shape is
the whole reason the matching rules are what they are. The process plumbing is
driven with a fake child so no browser is involved.
"""

from __future__ import annotations

import socket

from yeaboi.claude_auth import (
    _GRACE_POLLS,
    SubscriptionSignIn,
    extract_token,
    extract_url,
    setup_token_available,
    strip_ansi,
)

TOKEN = "sk-ant-oat01-AbC123_def-456XYZgh789ijkLMNop"
URL = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9"
    "&response_type=code&scope=user%3Ainference&code_challenge=Q-gI09C1lOn6-mUaHvZWWsl7G58xl2ri4AT3iy9nTJk"
)

# What the CLI actually puts on the wire: styled, cursor-positioned, spinner frames.
REAL_SHAPED = (
    b"\x1b[?25l\x1b[>4;2m\x1b[1mWelcome\x1b[3Cto\x1b[3CClaude\x1b[3CCode\x1b[0m\r\n"
    b"\x1b[38;5;3m\xe2\x9c\xa2\x1b[0m\r\x1b[38;5;3m\xe2\x9c\xb3\x1b[0m\r"
    b"Browser\x1b[3Cdidn't\x1b[3Copen?\x1b[3CUse\x1b[3Cthe\x1b[3Curl\x1b[3Cbelow\x1b[3C(c\x1b[3Cto\x1b[3Ccopy)\r\n"
    + URL.encode()
    + b"\r\n\r\nPaste\x1b[3Ccode\x1b[3Chere\x1b[3Cif\x1b[3Cprompted\x1b[3C> "
)


class TestStripAnsi:
    def test_removes_csi_and_osc(self):
        assert "\x1b" not in strip_ansi(REAL_SHAPED)

    def test_survives_undecodable_bytes(self):
        assert "ok" in strip_ansi(b"\xff\xfe ok")


class TestExtractUrl:
    def test_finds_the_authorize_url(self):
        assert extract_url(strip_ansi(REAL_SHAPED)) == URL

    def test_ignores_other_links(self):
        assert extract_url("see https://docs.claude.com/help for more") == ""

    def test_empty_before_it_is_printed(self):
        assert extract_url("Opening browser to sign in...") == ""


class TestExtractToken:
    def test_finds_the_token(self):
        assert extract_token(f"Success!\n{TOKEN}\n") == TOKEN

    def test_last_match_wins(self):
        raw = f"e.g. sk-ant-oat01-EXAMPLEPLACEHOLDER0000\n...\n{TOKEN}\n"
        assert extract_token(raw) == TOKEN

    def test_no_token_returns_empty(self):
        assert extract_token("Aborted.\n") == ""


class TestSetupTokenAvailable:
    def test_true_when_cli_on_path(self, monkeypatch):
        monkeypatch.setattr("yeaboi.claude_auth.shutil.which", lambda _: "/usr/local/bin/claude")
        assert setup_token_available() is True

    def test_false_when_missing(self, monkeypatch):
        monkeypatch.setattr("yeaboi.claude_auth.shutil.which", lambda _: None)
        assert setup_token_available() is False


class _FakeProc:
    """Stands in for the CLI: exits only when told to."""

    pid = 4242

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _session_on_a_socket(monkeypatch) -> tuple[SubscriptionSignIn, socket.socket, _FakeProc]:
    """A started session whose 'child' is the far end of a socketpair.

    A real fd pair (rather than a mock) keeps the non-blocking read, the select
    loop and the write-back under test — that plumbing is where this class can
    actually go wrong. It must be a socketpair and not a pipe: a pty master is
    bidirectional, and `send_code` writes to the same fd `poll` reads.
    """
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    proc = _FakeProc()
    monkeypatch.setattr("yeaboi.claude_auth.shutil.which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr("yeaboi.claude_auth.pty.openpty", lambda: (ours.fileno(), theirs.fileno()))
    monkeypatch.setattr("yeaboi.claude_auth.fcntl.ioctl", lambda *a, **k: None)
    # start() closes the slave fd; the socket objects own these, so leave them be.
    monkeypatch.setattr("yeaboi.claude_auth.os.close", lambda _fd: None)
    monkeypatch.setattr("yeaboi.claude_auth.subprocess.Popen", lambda *a, **k: proc)
    session = SubscriptionSignIn()
    assert session.start() is True
    return session, theirs, proc


class TestSubscriptionSignIn:
    def test_missing_cli_never_spawns(self, monkeypatch):
        monkeypatch.setattr("yeaboi.claude_auth.shutil.which", lambda _: None)
        session = SubscriptionSignIn()
        assert session.start() is False
        assert "not found" in session.error
        assert session.done is True

    def test_spawn_failure_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr("yeaboi.claude_auth.shutil.which", lambda _: "/usr/local/bin/claude")
        monkeypatch.setattr("yeaboi.claude_auth.pty.openpty", lambda: (_ for _ in ()).throw(OSError("no ptys")))
        session = SubscriptionSignIn()
        assert session.start() is False
        assert "no ptys" in session.error

    def test_url_then_prompt_then_token(self, monkeypatch):
        session, child, proc = _session_on_a_socket(monkeypatch)

        # Nothing yet: no URL, not asking for anything.
        session.poll()
        assert session.url == "" and not session.awaiting_code

        child.sendall(REAL_SHAPED)
        session.poll()
        assert session.url == URL
        assert session.awaiting_code is True
        assert session.done is False

        session.send_code("abc123")
        assert session.awaiting_code is False  # a sent code closes the field

        child.sendall(f"\r\n{TOKEN}\r\n".encode())
        session.poll()
        assert session.token == TOKEN
        assert session.done is True
        assert "Signed in" in session.message
        session.cancel()

    def test_the_code_challenge_in_the_url_is_not_mistaken_for_a_token(self, monkeypatch):
        # The URL carries `code_challenge=<base64ish>`, which matches the token
        # shape — so a token is only looked for once a code has been submitted.
        session, child, _ = _session_on_a_socket(monkeypatch)
        child.sendall(REAL_SHAPED)
        session.poll()
        assert session.token == ""
        assert session.done is False
        session.cancel()

    def test_exit_without_a_token_reports_failure(self, monkeypatch):
        session, _child, proc = _session_on_a_socket(monkeypatch)
        proc.returncode = 1
        # The child is owed several polls to have its last output read before this
        # is called a failure.
        for _ in range(_GRACE_POLLS):
            session.poll()
            assert session.done is False
        session.poll()
        assert session.token == ""
        assert "did not complete" in session.error
        session.cancel()

    def test_clean_exit_with_no_token_says_so(self, monkeypatch):
        session, _child, proc = _session_on_a_socket(monkeypatch)
        proc.returncode = 0
        for _ in range(_GRACE_POLLS + 1):
            session.poll()
        assert "no token" in session.error
        session.cancel()

    def test_a_token_buffered_at_exit_still_wins(self, monkeypatch):
        # The grace poll exists for exactly this: the child prints the token and
        # exits, and the first poll sees returncode before reading the token.
        session, child, proc = _session_on_a_socket(monkeypatch)
        child.sendall(REAL_SHAPED)
        session.poll()
        session.send_code("abc123")
        child.sendall(TOKEN.encode())
        proc.returncode = 0
        session.poll()
        assert session.token == TOKEN
        assert session.error == ""
        session.cancel()

    def test_send_code_ignores_blank_input(self, monkeypatch):
        session, _child, _ = _session_on_a_socket(monkeypatch)
        session.send_code("   ")
        assert session.code_sent is False
        session.cancel()

    def test_cancel_is_idempotent(self, monkeypatch):
        session, _child, _ = _session_on_a_socket(monkeypatch)
        session.cancel()
        session.cancel()  # must not raise
        session.poll()  # nor must polling a cancelled session
        assert session.message == "Sign-in cancelled"


class TestSubscriptionBubble:
    """Render tests for the duck's sign-in bubble.

    It is a bubble rather than a page so the settings behind it stay on screen, so
    these check both halves: the bubble itself, and that compositing it leaves the
    page underneath intact.
    """

    URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=abc&scope=user%3Ainference"

    @staticmethod
    def _text(cols: int = 68, **kwargs) -> str:
        import io

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens import _build_subscription_bubble

        console = Console(file=io.StringIO(), width=cols, force_terminal=False, legacy_windows=False)
        bubble = _build_subscription_bubble(cols=cols, **kwargs)
        lines = console.render_lines(bubble, console.options.update(width=cols, height=None), pad=True)
        return "\n".join("".join(s.text for s in line) for line in lines)

    def test_waiting_stage_offers_a_way_out(self):
        out = self._text(spinner="⠙")
        assert "opening your browser" in out
        assert "esc to cancel" in out

    def test_url_stage_shows_the_link_and_its_controls(self):
        out = self._text(url=self.URL)
        assert "sign in" in out  # the bubble's title
        assert "oauth/authorize" in out
        assert "tab copy" in out and "enter submit" in out and "esc cancel" in out

    def test_long_url_wraps_inside_the_bubble(self):
        # Every character is on screen — none of it is trimmed — and nothing
        # overruns the bubble.
        out = self._text(url=self.URL + "&padding=" + "x" * 300)
        assert "x" * 50 in out.replace("\n", "").replace(" ", "").replace("│", "")
        assert all(len(line) <= 68 for line in out.splitlines())

    def test_code_field_shows_the_cursor(self):
        assert "ABC123█" in self._text(url=self.URL, awaiting_code=True, code="ABC123", cursor=6)

    def test_copy_confirms_itself(self):
        assert "tab copied" in self._text(url=self.URL, copied=True)

    def test_success_stage(self):
        out = self._text(done=True, ok=True)
        assert "signed in" in out and "press any key" in out

    def test_failure_stage_carries_the_reason(self):
        out = self._text(done=True, ok=False, detail="Sign-in did not complete")
        assert "sign-in failed" in out and "Sign-in did not complete" in out


class TestSignInOverlay:
    """The bubble is composited over a finished frame, leaving it otherwise intact."""

    @staticmethod
    def _frame(width=118, height=40):
        import io

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        console = Console(file=io.StringIO(), width=width, height=height, force_terminal=False, legacy_windows=False)
        options = console.options.update(width=width, height=height)
        panel = _build_settings_screen({"LLM_PROVIDER": "anthropic"}, width=width, height=height, active_tab=0)
        return console, options, console.render_lines(panel, options, pad=True)

    @staticmethod
    def _as_text(lines) -> str:
        return "\n".join("".join(s.text for s in line) for line in lines)

    def test_it_draws_over_the_page_without_resizing_it(self):
        from yeaboi.ui.mode_select.screens._screens import _draw_signin_bubble

        console, options, lines = self._frame()
        before = self._as_text(lines)
        widths = [sum(s.cell_length for s in line) for line in lines]
        _draw_signin_bubble(console, options, lines, {"url": "https://claude.com/cai/oauth/authorize?a=1"})
        after = self._as_text(lines)

        assert after != before  # something was drawn
        assert "oauth/authorize" in after
        # The frame keeps its shape — an overlay must not reflow the page.
        assert len(lines) == len(widths)
        assert [sum(s.cell_length for s in line) for line in lines] == widths
        # The settings are still there behind it.
        assert "LLM Provider" in after

    def test_a_frame_too_small_is_left_alone(self):
        from yeaboi.ui.mode_select.screens._screens import _draw_signin_bubble

        console, options, lines = self._frame(width=40, height=12)
        before = self._as_text(lines)
        _draw_signin_bubble(console, options, lines, {"url": "https://claude.com/cai/oauth/authorize?a=1"})
        assert self._as_text(lines) == before  # too cramped — nothing drawn, nothing broken
