"""OS clipboard bridge — image paste (Ctrl+V) plus text in both directions.

Terminals cannot deliver image bytes through stdin: bracketed paste (see
``ui/shared/_input.py``) is text-only, and Cmd+V on macOS is intercepted by the
terminal emulator as a plain-text paste. So, like Claude Code, we bind **Ctrl+V**
and read the image straight off the OS clipboard by shelling out to
platform-native helpers — no new Python dependency, mirroring the external-binary
pattern of :mod:`yeaboi.voice` and :mod:`yeaboi.music`.

Design notes / architectural decisions:
- **Stdlib only.** macOS ships ``osascript``; Linux desktops usually have
  ``wl-paste`` (Wayland) or ``xclip`` (X11). We probe with ``shutil.which`` and
  degrade gracefully — a missing helper or an imageless clipboard returns ``None``,
  never raises into the TUI frame loop.
- **pngpaste fast path.** ``osascript`` round-trips the image as a hex dump
  (a 5 MB screenshot becomes ~10 MB of hex), which can take 0.5–2 s. If the user
  has `pngpaste` installed (``brew install pngpaste``) we use it instead — it
  writes raw PNG bytes to stdout in milliseconds.
- **PNG first, JPEG second.** macOS screenshots land on the clipboard as
  ``«class PNGf»``; images copied from browsers/Photos may only offer
  ``«class JPEG»``. We try both and report the mime type to the caller so the
  right extension and LLM ``mime_type`` are used. Files copied in Finder
  (``furl``) are deliberately not supported in v1.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

# Hard ceiling on how long any clipboard helper may run — a wedged osascript must
# never freeze the TUI for more than this.
_TIMEOUT_SECONDS = 10


def copy_text(text: str) -> bool:
    """Write ``text`` to the OS clipboard. Returns True on success, False otherwise.

    Mirrors the read-side helper-probing here: ``pbcopy`` on macOS, ``wl-copy``
    (Wayland) or ``xclip`` (X11) on Linux — probed with ``shutil.which`` and run with
    the same hard timeout. Like the image reader, it never raises into the TUI frame
    loop; a missing helper or any failure just returns False so the caller can show a
    "couldn't copy" notice and move on.
    """
    if not text:
        return False
    if sys.platform == "darwin":
        candidates: list[list[str]] = [["pbcopy"]]
    elif sys.platform.startswith("linux"):
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"]]
    else:
        logger.warning("clipboard text copy unsupported on platform: %s", sys.platform)
        return False

    data = text.encode("utf-8")
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, input=data, capture_output=True, timeout=_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.warning("clipboard copy helper %s failed: %s", cmd[0], exc)
            continue
        if proc.returncode == 0:
            logger.info("clipboard text copied: %d chars via %s", len(text), cmd[0])
            return True
        logger.warning("clipboard copy helper %s exited %d", cmd[0], proc.returncode)
    logger.info("no clipboard copy helper available")
    return False


def read_clipboard_text() -> str | None:
    """Read plain text off the OS clipboard. Returns ``None`` when unavailable.

    The mirror of :func:`copy_text`, and the ONLY workable way to paste a
    transcript. Bracketed paste (``ui/shared/_input.py``) strips every newline
    and caps at 10 000 chars, which would turn a standup into one unattributed
    turn — see ``standup/transcripts.py`` for why that silently degrades a review.
    Reading the clipboard directly keeps the line structure the parsers need.
    """
    if sys.platform == "darwin":
        candidates: list[list[str]] = [["pbpaste"]]
    elif sys.platform.startswith("linux"):
        candidates = [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]]
    else:
        logger.warning("clipboard text read unsupported on platform: %s", sys.platform)
        return None

    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.warning("clipboard read helper %s failed: %s", cmd[0], exc)
            continue
        if proc.returncode != 0:
            logger.warning("clipboard read helper %s exited %d", cmd[0], proc.returncode)
            continue
        text = proc.stdout.decode("utf-8", errors="replace")
        logger.info("clipboard text read: %d chars via %s", len(text), cmd[0])
        return text
    logger.info("no clipboard read helper available")
    return None


def copy_markdown_status(text: str) -> str:
    """Copy ``text`` to the clipboard and return a user-facing status message.

    Thin wrapper over :func:`copy_text` shared by every "Copy to clipboard"
    surface (the export picker's copy destination + the Usage/Changelog pages):
    keeps the success/failure wording in one place. Never raises.
    """
    if not (text or "").strip():
        return "Nothing to copy"
    if copy_text(text):
        return "Copied to clipboard"
    return "Couldn't copy — no clipboard helper available (see logs)"


def read_clipboard_image() -> tuple[bytes, str] | None:
    """Return ``(image_bytes, mime_type)`` from the OS clipboard, or ``None``.

    ``mime_type`` is ``"image/png"`` or ``"image/jpeg"``. Returns ``None`` when the
    clipboard holds no image, the platform helpers are missing, or anything goes
    wrong — callers show a "No image on clipboard" notice and move on.
    """
    try:
        if sys.platform == "darwin":
            result = _read_macos_clipboard()
        elif sys.platform.startswith("linux"):
            result = _read_linux_clipboard()
        else:
            logger.warning("clipboard image paste unsupported on platform: %s", sys.platform)
            return None
    except Exception as exc:  # defensive: clipboard access must never crash the TUI
        logger.warning("clipboard image read failed: %s", exc)
        return None

    if result:
        data, mime = result
        logger.info("clipboard image read: %d bytes (%s)", len(data), mime)
    else:
        logger.info("no image found on clipboard")
    return result


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    """Run a clipboard helper, returning None on timeout/missing binary."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("clipboard helper %s failed: %s", cmd[0], exc)
        return None


def _read_macos_clipboard() -> tuple[bytes, str] | None:
    """Read an image from the macOS clipboard via pngpaste (fast) or osascript."""
    # Fast path: pngpaste writes raw PNG bytes to stdout ("-" = stdout).
    if shutil.which("pngpaste"):
        proc = _run(["pngpaste", "-"])
        if proc and proc.returncode == 0 and proc.stdout:
            return proc.stdout, "image/png"

    # osascript coerces the clipboard to a typed data blob rendered as
    # «data PNGf<hex>» on stdout; a non-image clipboard makes the coercion fail
    # with a non-zero exit ("Can't make some data into the expected type").
    for cls, mime in (("PNGf", "image/png"), ("JPEG", "image/jpeg")):
        proc = _run(["osascript", "-e", f"the clipboard as «class {cls}»"])
        if not proc or proc.returncode != 0:
            continue
        # osascript prints the guillemet-wrapped hex dump in UTF-8 («data PNGf…»).
        data = _parse_osascript_hex(proc.stdout.decode("utf-8", errors="replace"), cls)
        if data:
            return data, mime
    return None


def _parse_osascript_hex(out: str, cls: str) -> bytes | None:
    """Decode osascript's ``«data PNGf89504E47...»`` hex dump into raw bytes."""
    out = out.strip()
    prefix = f"«data {cls}"
    if not (out.startswith(prefix) and out.endswith("»")):
        return None
    hex_str = out[len(prefix) : -1].strip()
    try:
        return bytes.fromhex(hex_str) or None
    except ValueError:
        logger.warning("could not decode osascript clipboard hex (%d chars)", len(hex_str))
        return None


def _read_linux_clipboard() -> tuple[bytes, str] | None:
    """Read an image from the Linux clipboard via wl-paste (Wayland) or xclip (X11)."""
    candidates = [
        (["wl-paste", "--type", "image/png"], "image/png"),
        (["xclip", "-selection", "clipboard", "-t", "image/png", "-o"], "image/png"),
    ]
    for cmd, mime in candidates:
        if not shutil.which(cmd[0]):
            continue
        proc = _run(cmd)
        if proc and proc.returncode == 0 and proc.stdout:
            return proc.stdout, mime
    return None
