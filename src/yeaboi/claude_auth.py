"""Mint a Claude subscription token by driving the Claude Code CLI in-process.

``claude setup-token`` is interactive: it opens a browser, prints an authorize
URL, blocks on ``Paste code here if prompted >``, and prints a long-lived OAuth
token once the pasted code checks out.

Handing it the real terminal would work but throws the user out of the TUI, so
instead it runs on a pty *we* own: :class:`SubscriptionSignIn` spawns it, reads
its output without blocking, and exposes the three things a screen needs — the
URL to show, whether the child is waiting for a code, and the token when it
lands. The TUI polls it a frame at a time and stays up throughout.

Two details the pty forces:

- **Width matters.** The child wraps its output to the terminal it thinks it has,
  and the authorize URL is long enough to be split across lines at any normal
  width. The pty is sized far wider than any real terminal so the URL arrives on
  one line and needs no unwrapping.
- **Spaces are not spaces.** The CLI positions its text with cursor-movement
  escapes rather than literal spaces, so stripping ANSI leaves words run
  together ("Pastecodehere"). Nothing here may match on a phrase with spaces in
  it; the URL (which has none) and single words are safe.

The token is not an API key — it authenticates as ``Authorization: Bearer`` with
the ``oauth-2025-04-20`` beta header. See ``agent/llm.py``.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios

logger = logging.getLogger(__name__)

SETUP_TOKEN_CMD = ("claude", "setup-token")

# Wider than any real terminal, so the authorize URL is never wrapped by the
# child. 200 columns is comfortably past the ~330-character URL's wrap point at
# any width a user would actually have.
_PTY_COLS = 400
_PTY_ROWS = 60

# Claude Code OAuth tokens are `sk-ant-oat<NN>-<base64ish>`. Loose on the middle
# segment so a future revision still matches, anchored on the prefix so it cannot
# pick a word out of the surrounding prose.
_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9]*-?[A-Za-z0-9_\-]{20,}")

# The sign-in URL the child prints when the browser does not open (and alongside
# it when it does). Anchored on the oauth authorize path so a docs link in the
# same output cannot be mistaken for it.
_URL_RE = re.compile(r"https://\S*?/oauth/authorize\?\S+")

# ANSI CSI and OSC sequences. Cursor movement is what makes the spinner and the
# layout, so this runs before any matching.
# The parameter class includes < > = ? : the CLI emits private-mode sequences like
# `\x1b[>4;2m` and `\x1b[?25l`, and a class of digits alone leaves them on screen.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?<>=:]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>]|\x1b\][0-9;]*")

# Word the "Paste code here if prompted >" line reduces to once the cursor-motion
# escapes between its words are stripped. Matching the whole phrase would fail.
_PROMPT_WORD = "Paste"

# Polls a just-exited child is given to have its last output read before the flow
# calls it a failure. At one frame each this is a few tens of milliseconds.
_GRACE_POLLS = 5


def setup_token_available() -> bool:
    """True when the Claude Code CLI is on PATH and can mint a token."""
    return shutil.which(SETUP_TOKEN_CMD[0]) is not None


def strip_ansi(raw: bytes) -> str:
    """Terminal output as plain text, with escape sequences removed."""
    return _ANSI_RE.sub("", raw.decode("utf-8", "replace"))


def extract_token(text: str) -> str:
    """The OAuth token in ``text``, or ``""``.

    Last match wins: the CLI may print a placeholder in its instructions before
    the real token, and the real one is always last.
    """
    matches = _TOKEN_RE.findall(text)
    return matches[-1] if matches else ""


def extract_url(text: str) -> str:
    """The authorize URL in ``text``, or ``""`` (first match — it prints once)."""
    match = _URL_RE.search(text)
    return match.group(0) if match else ""


class SubscriptionSignIn:
    """A running ``claude setup-token``, driven a poll at a time.

    Lifecycle: :meth:`start`, then :meth:`poll` every frame until :attr:`done`,
    calling :meth:`send_code` when :attr:`awaiting_code` turns True. :meth:`cancel`
    at any point. Every method is safe to call in any state — a credential flow
    that raises into the render loop would take the TUI down with it.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._master: int | None = None
        self._buf = bytearray()
        self.text = ""  # everything the child has printed, ANSI stripped
        self.url = ""
        self.token = ""
        self.error = ""
        self.code_sent = False
        self._grace_polls = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        """Spawn the CLI. False (with :attr:`error` set) if it could not start."""
        if not setup_token_available():
            self.error = "Claude Code CLI not found — install it to sign in with a subscription"
            logger.warning("setup-token: `claude` not found on PATH")
            return False
        try:
            master, slave = pty.openpty()
            # Size the child's terminal before it writes anything, or its first
            # lines are wrapped to the default 80 columns.
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", _PTY_ROWS, _PTY_COLS, 0, 0))
            os.set_blocking(master, False)
            self._proc = subprocess.Popen(
                list(SETUP_TOKEN_CMD),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave)
            self._master = master
        except Exception as exc:  # noqa: BLE001 - any spawn failure is reportable, not fatal
            self.error = f"Could not run `claude setup-token`: {exc}"
            logger.warning("setup-token: spawn failed: %s", exc)
            return False
        logger.info("setup-token: started (pid %s)", self._proc.pid)
        return True

    def poll(self) -> None:
        """Drain whatever the child has written and update the parsed state.

        Never blocks: the master fd is non-blocking and ``select`` is called with
        a zero timeout, so this costs nothing to call every frame.
        """
        if self._master is None:
            return
        while True:
            try:
                ready, _, _ = select.select([self._master], [], [], 0)
            except (OSError, ValueError):
                break
            if not ready:
                break
            try:
                chunk = os.read(self._master, 8192)
            except OSError as exc:
                # EIO is the normal read on a pty whose child has exited.
                if exc.errno not in (errno.EIO, errno.EAGAIN, errno.EWOULDBLOCK):
                    logger.debug("setup-token: read failed: %s", exc)
                break
            if not chunk:
                break
            self._buf.extend(chunk)

        self.text = strip_ansi(bytes(self._buf))
        if not self.url:
            self.url = extract_url(self.text)
        if not self.token:
            # Matched on every poll, not only after a code was submitted. Gating on
            # the submission looked like cheap insurance against the URL's
            # `code_challenge=<base64ish>` — but the pattern is anchored on the
            # literal `sk-ant-`, which no part of the URL contains, so the gate
            # protected nothing and lost the token whenever the browser callback
            # completed the flow without the user pasting anything.
            found = extract_token(self.text)
            if found:
                self.token = found
                logger.info("setup-token: token captured (%d chars)", len(found))

        if self.token or self.error:
            return
        if self._proc is not None and self._proc.poll() is not None and not self._take_grace_poll():
            # Exited without printing a token — say which way it failed.
            self.error = (
                "Sign-in did not complete"
                if self._proc.returncode
                else "Sign-in finished but no token was returned — paste a key instead"
            )
            logger.warning(
                "setup-token: exited %s with no token; last output: %r",
                self._proc.returncode,
                self.text[-400:],
            )

    def _take_grace_poll(self) -> bool:
        """Consume one of the extra drains a just-exited child is owed.

        A token printed immediately before exit can still be unread when ``poll()``
        first sees ``returncode`` — and on macOS a pty master starts reporting EIO
        once the child is gone, so there is no second chance later. Several polls
        rather than one, because reporting failure on the run that just succeeded
        is the worst outcome this class has and the wait costs a few frames.
        """
        if self._grace_polls >= _GRACE_POLLS:
            return False
        self._grace_polls += 1
        return True

    def send_code(self, code: str) -> None:
        """Write the pasted authorization code to the child's stdin."""
        if self._master is None or not code.strip():
            return
        try:
            os.write(self._master, code.strip().encode() + b"\r")
            self.code_sent = True
            logger.info("setup-token: code submitted")
        except OSError as exc:
            self.error = f"Could not send the code: {exc}"
            logger.warning("setup-token: write failed: %s", exc)

    def cancel(self) -> None:
        """Stop the child and release the pty. Safe to call more than once."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                # start_new_session put it in its own process group, so the whole
                # group goes — the CLI may have spawned a browser opener.
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001 - teardown must not raise into the UI
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    logger.debug("setup-token: could not kill child", exc_info=True)
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None

    # -- state -------------------------------------------------------------
    @property
    def awaiting_code(self) -> bool:
        """True once the child has asked for the code and none has been sent."""
        return bool(self.url) and _PROMPT_WORD in self.text and not self.code_sent and not self.done

    @property
    def done(self) -> bool:
        return bool(self.token or self.error)

    @property
    def message(self) -> str:
        """The one-line result for the settings status line."""
        if self.token:
            return "Signed in — subscription token saved"
        return self.error or "Sign-in cancelled"
