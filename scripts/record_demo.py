#!/usr/bin/env python3
"""Record the README demo GIF deterministically — no human, no asciinema.

The old pipeline was a person running ``asciinema rec`` and typing through the
TUI live: keystroke timing, terminal geometry, the PyPI update check, and the
recorder's real ``~/.scrum-agent`` contents were all baked into the output, and
the ``.cast`` source was thrown away. This script replaces every one of those
variables:

- spawns ``yeaboi --dry-run`` in a pty pinned to 140x40 with a hermetic HOME
  (the exact recipe proven by ``tests/integration/test_tui_smoke.py``);
- drives a fixed key script, synchronized on rendered screen markers — never
  on sleeps — so the recording survives slow machines;
- writes the asciinema v2 ``.cast`` itself from the pty byte stream (agg reads
  the cast directly, so asciinema is not needed at all);
- renders the GIF with pinned agg flags and then *verifies* the result, failing
  loudly on blank/frozen frames or absurd durations. (The corruption that
  shipped before — garbled block-glyph titles full of U+FFFD — came from
  multibyte glyphs split across read chunks; CastWriter's incremental decoder
  makes that impossible.)

The recording is structurally deterministic (same screens, same keys, same
duration), not byte-identical — tips, shimmer and the duck animate off the wall
clock, which is accepted cosmetic variance.

POSIX-only (pty/termios), like the TUI itself.

Usage::

    uv run python scripts/record_demo.py                # record + render + verify
    uv run python scripts/record_demo.py --render-only  # re-render GIF from committed cast
    uv run python scripts/record_demo.py --check-only   # verify the existing cast + GIF

``verify()`` needs Pillow, which the dev venv gets transitively via matplotlib
(the ``charts`` extra); rendering needs ``agg`` (``brew install agg``).
"""

from __future__ import annotations

import argparse
import codecs
import gzip
import json
import logging
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("record_demo")

REPO_ROOT = Path(__file__).resolve().parents[1]
# Committed gzipped: the raw cast is ~10MB of 60fps full-screen repaints that
# compress ~100:1; the .gz suffix switches CastWriter/render/verify to gzip.
DEFAULT_CAST = REPO_ROOT / "docs" / "demo.cast.gz"
DEFAULT_GIF = REPO_ROOT / "docs" / "demo.gif"

COLS, ROWS = 140, 40

# Same markers and ANSI regex as tests/integration/test_tui_smoke.py — screen
# chrome that appears once mode-select has rendered, matched after stripping.
MODE_SCREEN_MARKERS = ("changelog", "Tip:", "channel")

# Chrome of the Humans/Agents landing split (Phase 0), which renders BEFORE any
# mode menu. Deliberately ONE fragment, from the heading, and not the key hints
# the smoke test also matches on: the two sets must be disjoint on the *rendered
# screens*, not merely as literals. The mode menu's rotating tip bar carries
# tips containing "switch" and "choose", so matching those would let the
# post-Esc `await` resolve against the menu it is leaving and race the
# transition — and would hide a swallowed Esc instead of failing loudly.
# test_record_demo.py renders both screens and asserts the disjointness.
CATEGORY_SCREEN_MARKERS = ("working with",)
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][0-9A-B]"
    r"|\x1b[=>]"
)

_ALT_SCREEN = b"\x1b[?1049h"
_ALT_SCREEN_EXIT = "\x1b[?1049l"

KEY_UP, KEY_DOWN, KEY_RIGHT, KEY_LEFT = b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D"
KEY_ENTER, KEY_ESC = b"\r", b"\x1b"

# The demo choreography — the whole product in one take: the landing split, the
# Humans menu, back out, then the Agents family.
#
# Two rules hold this together, both load-bearing:
#
# 1. Every screen change is an `await` on that screen's markers, never a
#    `pause`. A pause long enough to cover a slow machine's menu build would
#    also be a pause the fast path sits through; markers make the recording
#    both quick and machine-independent.
# 2. A key step is NEVER placed immediately after KEY_ESC. read_key treats a
#    lone \x1b as Escape only when no second byte arrives within 100ms
#    (src/yeaboi/ui/shared/_input.py:158-165) — a key written straight after it
#    would be swallowed as an escape sequence. Every step following KEY_ESC
#    here is an `await`, i.e. a drained read far longer than 100ms.
#    tests/unit/test_record_demo.py pins this.
DEMO_SCRIPT: list[tuple] = [
    ("await", CATEGORY_SCREEN_MARKERS, 30.0),  # splash plays through; sync on the split
    ("pause", 2.5),  # both world-cards settle: duck left, robo-duck right
    ("key", KEY_RIGHT),
    ("pause", 1.2),  # Agents wakes — accent border, tinted interior, wing flap
    ("key", KEY_LEFT),
    ("pause", 1.0),  # back on Humans
    ("key", KEY_ENTER),
    ("await", MODE_SCREEN_MARKERS, 15.0),  # the nine Humans cards sweep in
    ("pause", 1.2),
    ("key", KEY_DOWN),
    ("pause", 0.8),
    ("key", KEY_DOWN),
    ("pause", 0.8),
    ("key", KEY_RIGHT),
    ("pause", 0.8),
    ("key", KEY_UP),
    ("pause", 1.2),  # settle on a card, let the description reveal finish
    ("key", KEY_ESC),  # esc from a menu returns to the split (q would quit)
    ("await", CATEGORY_SCREEN_MARKERS, 15.0),
    ("pause", 0.8),
    ("key", KEY_RIGHT),
    ("pause", 0.6),
    ("key", KEY_ENTER),
    ("await", MODE_SCREEN_MARKERS, 15.0),  # Agents: same builder, three cards
    ("pause", 1.2),
    ("key", KEY_DOWN),
    ("pause", 1.0),
    ("key", KEY_DOWN),
    ("pause", 1.5),  # rest on Security so the last frame is a real screen
    ("key", b"q"),
]

AGG_FLAGS = [
    "--theme",
    "github-dark",
    "--font-size",
    "14",
    "--fps-cap",
    "24",
    "--idle-time-limit",
    "2",
    "--last-frame-duration",
    "2",
]

# verify() bounds: generous enough to survive script tweaks, tight enough to
# kill the observed failure modes. Note it is the DURATION range alone that
# catches the 209-frames-x-3000ms case — 209 frames is a fine frame count, so
# do not loosen the duration bound expecting the frame bound to backstop it.
GIF_MAX_BYTES = 6 * 1024 * 1024
GIF_FRAMES_RANGE = (50, 1500)
GIF_DURATION_RANGE_S = (6.0, 45.0)
GIF_MIN_DISTINCT_COLORS = 64
CAST_DURATION_RANGE_S = (6.0, 40.0)
CAST_MIN_EVENTS = 100


class CastWriter:
    """Incrementally write an asciinema v2 cast from raw pty bytes.

    Three transforms, all safe because a cast is a replayed byte stream (frame
    boundaries carry no meaning):

    - timestamps are rebased so the first output lands at ~0.1s — python
      startup dead air never reaches the GIF;
    - chunks are coalesced (flush after 25ms or 64KiB) to cut JSON overhead
      without touching payload bytes;
    - decoding is incremental, so a multibyte glyph split across two
      ``os.read()`` calls can never inject U+FFFD mid-escape-sequence.
    """

    FLUSH_AFTER_S = 0.025
    FLUSH_AFTER_BYTES = 64 * 1024
    LEAD_IN_S = 0.1

    def __init__(self, path: Path, cols: int = COLS, rows: int = ROWS) -> None:
        self.path = path
        self._fh = gzip.open(path, "wt", encoding="utf-8") if path.suffix == ".gz" else path.open("w", encoding="utf-8")
        header = {
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(time.time()),
            "env": {"TERM": "xterm-256color", "SHELL": "/bin/sh"},
            "title": "yeaboi --dry-run",
        }
        self._fh.write(json.dumps(header) + "\n")
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._t0: float | None = None
        self._buffer = ""
        self._buffer_bytes = 0
        self._buffer_start = 0.0
        self.events = 0
        self.last_t = 0.0
        # Alt-screen entry is the strongest signal the live terminal path (not a
        # fallback print) is running. Watched here — with a carry across feeds —
        # because record()'s bounded tail may have dropped the early bytes.
        self.saw_alt_screen = False
        self._watch_carry = ""
        # Once the TUI leaves the alt screen (quit), the cast is over: recording
        # the restored bare terminal would hold a blank final GIF frame for
        # --last-frame-duration on every README loop.
        self._ended = False
        self._end_carry = ""

    def feed(self, chunk: bytes, now: float) -> None:
        if self._ended:
            return
        if self._t0 is None:
            self._t0 = now - self.LEAD_IN_S
        text = self._decoder.decode(chunk)
        if not text:
            return
        if not self.saw_alt_screen:
            watch = _ALT_SCREEN.decode()
            probe = self._watch_carry + text
            self.saw_alt_screen = watch in probe
            self._watch_carry = probe[-(len(watch) - 1) :]
        probe = self._end_carry + text
        idx = probe.find(_ALT_SCREEN_EXIT)
        if idx >= 0:
            self._ended = True
            # Keep only what precedes the exit sequence. If its head was already
            # written in an earlier feed (idx < carry length), a dangling escape
            # prefix stays in the cast — agg renders nothing for it.
            text = text[: max(idx - len(self._end_carry), 0)]
            if not text:
                return
        else:
            self._end_carry = probe[-(len(_ALT_SCREEN_EXIT) - 1) :]
        # Flush a stale buffer BEFORE appending: text arriving after a quiet gap
        # must start a fresh event stamped at its own time, or it replays early.
        if self._buffer and now - self._buffer_start >= self.FLUSH_AFTER_S:
            self._flush()
        if not self._buffer:
            self._buffer_start = now
        self._buffer += text
        self._buffer_bytes += len(chunk)
        if self._buffer_bytes >= self.FLUSH_AFTER_BYTES:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer or self._t0 is None:
            return
        t = max(self._buffer_start - self._t0, self.last_t)
        self._fh.write(json.dumps([round(t, 4), "o", self._buffer], ensure_ascii=False) + "\n")
        self.events += 1
        self.last_t = t
        self._buffer = ""
        self._buffer_bytes = 0

    def close(self) -> None:
        self._buffer += self._decoder.decode(b"", final=True)
        self._flush()
        self._fh.close()


def _strip_ansi(raw: bytes) -> str:
    return _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))


def _recording_env(home: Path) -> dict[str, str]:
    """The hermetic child environment for a recording.

    Mirrors ``_spawn_tui_in_pty`` in tests/integration/test_tui_smoke.py, plus
    the recording hardening: the PyPI update check, telemetry and tunnels are
    all switched off so nothing outside this process can repaint the screen.
    (``YEABOI_UPDATE_CHECK=0`` is the whole reason that env gate exists in
    ``update_check.py`` — keep the two in sync.)
    """
    env = {
        **os.environ,
        "HOME": str(home),
        "TERM": "xterm-256color",
        "LOG_LEVEL": "ERROR",
        "ANTHROPIC_API_KEY": "test-key-dry-run-only",
        "YEABOI_UPDATE_CHECK": "0",
        "YEABOI_NO_TUNNEL": "1",
        "YEABOI_TELEMETRY": "off",
    }
    env.pop("YEABOI_HOME", None)
    return env


def _spawn_tui(home: Path, cmd: list[str] | None = None) -> tuple[subprocess.Popen, int]:
    """Launch the TUI attached to a fresh pty; return (proc, master_fd)."""
    import fcntl
    import termios

    (home / ".yeaboi").mkdir(parents=True, exist_ok=True)
    (home / ".yeaboi" / ".env").write_text("ANTHROPIC_API_KEY=test-key-dry-run-only\n")
    env = _recording_env(home)

    master_fd, slave_fd = os.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

    proc = subprocess.Popen(
        cmd or [sys.executable, "-m", "yeaboi.cli", "--dry-run"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave_fd)
    return proc, master_fd


def _pump(
    master_fd: int,
    proc: subprocess.Popen,
    cast: CastWriter,
    seconds: float,
    predicate=None,
    tail_limit: int = 262_144,
) -> tuple[bytes, bool]:
    """Drain the pty into the cast for up to ``seconds``.

    Returns (bounded tail of raw bytes, predicate matched?). With a predicate,
    returns as soon as it matches the ANSI-stripped tail; without one, drains
    for the full duration (this is how scripted pauses stay flood-safe — the
    child repaints at 60fps and blocks the moment we stop reading).
    Only the last ``tail_limit`` bytes are kept/stripped: re-stripping an
    unbounded buffer every poll throttles the drain quadratically (see
    tests/integration/test_tui_smoke.py).
    """
    deadline = time.monotonic() + seconds
    tail = b""
    while True:
        now = time.monotonic()
        if now >= deadline:
            return tail, False
        ready, _, _ = select.select([master_fd], [], [], min(0.025, deadline - now))
        if ready:
            try:
                chunk = os.read(master_fd, 65536)
            except OSError:  # pty closed — process exited
                return tail, False
            if not chunk:
                return tail, False
            cast.feed(chunk, time.monotonic())
            tail = (tail + chunk)[-tail_limit:]
            if predicate is not None and predicate(_strip_ansi(tail)):
                return tail, True
        elif proc.poll() is not None:
            return tail, False


def record(cast_path: Path, cmd: list[str] | None = None, script: list[tuple] | None = None) -> None:
    """Run the demo script against a fresh TUI and write the cast. Raises on any failure."""
    script = DEMO_SCRIPT if script is None else script
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    cast = CastWriter(cast_path)
    with tempfile.TemporaryDirectory(prefix="yeaboi-demo-home-") as tmp:
        proc, master_fd = _spawn_tui(Path(tmp), cmd)
        try:
            for step in script:
                kind = step[0]
                if kind == "await":
                    _, markers, timeout = step
                    tail, matched = _pump(
                        master_fd, proc, cast, timeout, predicate=lambda text, _m=markers: any(m in text for m in _m)
                    )
                    if not matched:
                        raise RuntimeError(
                            f"screen markers {markers} never rendered (exit={proc.poll()}); "
                            f"last output:\n{_strip_ansi(tail)[-2000:]}"
                        )
                elif kind == "pause":
                    _pump(master_fd, proc, cast, step[1])
                elif kind == "key":
                    logger.info("key: %r", step[1])
                    os.write(master_fd, step[1])
                else:
                    raise ValueError(f"unknown script step: {step!r}")

            if not cast.saw_alt_screen:
                raise RuntimeError("TUI never entered the alternate screen buffer — not the live terminal path")

            # Drain until EOF so the pty can't block the child's final writes.
            _pump(master_fd, proc, cast, 15.0)
            returncode = proc.wait(timeout=15)
            if returncode != 0:
                raise RuntimeError(f"TUI did not exit cleanly (returncode={returncode})")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            os.close(master_fd)
            cast.close()
    logger.info(
        "cast written: %s (%d events, %.1fs, %d bytes)", cast_path, cast.events, cast.last_t, cast_path.stat().st_size
    )


def render(cast_path: Path, gif_path: Path) -> None:
    """Render the GIF from the cast with pinned agg flags."""
    agg = shutil.which("agg")
    if agg is None:
        sys.exit("agg not found — install it with: brew install agg")
    src = cast_path
    if cast_path.suffix == ".gz":
        # agg reads plain casts; inflate the committed .gz next to a temp path.
        fd, name = tempfile.mkstemp(suffix=".cast")
        os.close(fd)
        tmp = Path(name)
        tmp.write_bytes(gzip.decompress(cast_path.read_bytes()))
        src = tmp
    try:
        cmd = [agg, str(src), str(gif_path), *AGG_FLAGS]
        logger.info("rendering: %s", " ".join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        if src is not cast_path:
            src.unlink(missing_ok=True)
    logger.info("gif written: %s (%d bytes)", gif_path, gif_path.stat().st_size)


def _read_cast_text(cast_path: Path) -> str:
    raw = cast_path.read_bytes()
    if cast_path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")


def verify(cast_path: Path, gif_path: Path) -> list[str]:
    """Sanity-check both artifacts; return a list of problems (empty == sane)."""
    problems: list[str] = []
    for path in (cast_path, gif_path):
        if not path.is_file():
            problems.append(f"missing artifact: {path}")
    if problems:
        return problems

    lines = _read_cast_text(cast_path).splitlines()
    events = [json.loads(line) for line in lines[1:] if line]
    if len(events) < CAST_MIN_EVENTS:
        problems.append(f"cast has only {len(events)} events (expected >= {CAST_MIN_EVENTS})")
    span = events[-1][0] if events else 0.0
    if not (CAST_DURATION_RANGE_S[0] <= span <= CAST_DURATION_RANGE_S[1]):
        problems.append(f"cast spans {span:.1f}s (expected {CAST_DURATION_RANGE_S[0]}-{CAST_DURATION_RANGE_S[1]}s)")
    if _ALT_SCREEN.decode() not in "".join(e[2] for e in events):
        problems.append("cast never enters the alternate screen buffer")

    from PIL import Image

    size = gif_path.stat().st_size
    if size > GIF_MAX_BYTES:
        problems.append(f"gif is {size} bytes (limit {GIF_MAX_BYTES})")
    # Frames MUST be read via seek(), one at a time: ImageSequence.Iterator
    # yields the same underlying Image object mutated in place, so collecting
    # frames into a list silently reads every one at the last seek position.
    with Image.open(gif_path) as im:
        n = getattr(im, "n_frames", 1)
        if not (GIF_FRAMES_RANGE[0] <= n <= GIF_FRAMES_RANGE[1]):
            problems.append(f"gif has {n} frames (expected {GIF_FRAMES_RANGE[0]}-{GIF_FRAMES_RANGE[1]})")
        total_ms = 0
        for i in range(n):
            im.seek(i)
            total_ms += im.info.get("duration", 0)
        total_s = total_ms / 1000
        if not (GIF_DURATION_RANGE_S[0] <= total_s <= GIF_DURATION_RANGE_S[1]):
            problems.append(
                f"gif plays for {total_s:.1f}s (expected {GIF_DURATION_RANGE_S[0]}-{GIF_DURATION_RANGE_S[1]}s) "
                "— per-frame durations are broken"
            )
        # Sample frames across the animation. Mean luminance cannot tell a blank
        # dark screen from the rendered menu on a dark theme, but color richness
        # can: the menu's colored block-glyph titles use dozens of palette
        # entries, a blank/black frame uses a couple.
        color_peak = 0
        last_colors = 0
        signatures = set()
        for i in sorted({(n - 1) * k // 4 for k in range(5)} if n >= 5 else {0}):
            im.seek(i)
            rgb = im.convert("RGB")
            signatures.add(rgb.tobytes())
            colors = rgb.getcolors(4096)
            count = 4097 if colors is None else len(colors)
            color_peak = max(color_peak, count)
            if i == n - 1:
                last_colors = count
        if len(signatures) < 2:
            problems.append("all sampled frames are identical — frozen recording")
        if color_peak < GIF_MIN_DISTINCT_COLORS:
            problems.append(
                f"sampled frames peak at {color_peak} distinct colors "
                f"(expected >= {GIF_MIN_DISTINCT_COLORS}) — blank recording"
            )
        elif last_colors < GIF_MIN_DISTINCT_COLORS:
            # --last-frame-duration holds the final frame on every README loop;
            # a bare post-quit terminal there means the cast ran past the
            # alt-screen exit. The peak check above cannot see this.
            problems.append(f"final frame has only {last_colors} distinct colors — the gif ends on a blank screen")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record/render/verify the README demo GIF.")
    parser.add_argument("--cast", type=Path, default=DEFAULT_CAST)
    parser.add_argument("--gif", type=Path, default=DEFAULT_GIF)
    parser.add_argument("--render-only", action="store_true", help="skip recording; re-render from the existing cast")
    parser.add_argument("--check-only", action="store_true", help="verify the existing cast + gif and exit")
    parser.add_argument("--cmd", nargs="+", help=argparse.SUPPRESS)  # test seam: stub child process
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.check_only:
        if not args.render_only:
            if sys.platform == "win32":
                sys.exit("record_demo.py needs a POSIX pty; record on macOS or Linux")
            record(args.cast, cmd=args.cmd)
        render(args.cast, args.gif)

    problems = verify(args.cast, args.gif)
    if problems:
        for p in problems:
            logger.error("verify: %s", p)
        return 1
    logger.info("verify: cast + gif look sane")
    return 0


if __name__ == "__main__":
    sys.exit(main())
