#!/usr/bin/env python3
"""Seeded keystroke fuzzing of the live TUI, in a pty, with the world shut off.

`tests/integration/test_tui_smoke.py` proves the terminal path boots and quits.
This drives the same path with *adversarial* input: escape sequences, control
characters, resizes, oversized pastes, combining and right-to-left text — the
input a real terminal produces and no screen-builder unit test ever sees,
because those call the handlers directly with a fake key callable.

**Every run is reproducible.** A find carries the seed and the exact key
sequence that produced it, which is what makes a crash found this way an
auto-lane `bug` under `cowork/house-rules.md` — a mechanical reproduction is
the admission ticket, and the seed *is* the regression test.

Three things make it safe to run unattended:

``--dry-run``
    The CLI's own flag. The modes that thread it make no LLM calls at all.
**A closed network.**
    `HTTPS_PROXY` and `HTTP_PROXY` point at a port nothing listens on, so a
    mode that does *not* thread `dry_run` fails to connect rather than
    spending money. That failure is itself worth fuzzing — it is the error
    path a user on a plane takes — but it is not a crash, and
    `.github/hygiene/lens-policy.yml` excludes it by name.
**A temporary `HOME`.**
    Sessions, exports, `.env` and every log go to a directory that is deleted
    afterwards. `YEABOI_NO_TUNNEL=1` keeps a fuzzed retro board off the
    internet.

Usage::

    uv run python scripts/tui_fuzz.py --seeds 6 --steps 120
    uv run python scripts/tui_fuzz.py --seed 41 --steps 400 --json   # reproduce one
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][0-9A-B]"
    r"|\x1b[=>]"
)

# The alphabet, weighted. Navigation dominates on purpose: uniform random bytes
# quit on the first `q` and never reach a second screen, so the fuzzer would
# spend its whole budget re-testing the one path the smoke test already covers.
_KEYS: tuple[tuple[bytes, int], ...] = (
    (b"\x1b[A", 8),  # up
    (b"\x1b[B", 8),  # down
    (b"\x1b[C", 6),  # right
    (b"\x1b[D", 6),  # left
    (b"\r", 7),  # enter
    (b"\t", 4),  # tab
    (b"\x1b", 5),  # bare escape — the one that races the escape-sequence parser
    (b"\x1b[5~", 2),  # page up
    (b"\x1b[6~", 2),  # page down
    (b"\x7f", 3),  # backspace
    (b" ", 3),
    (b"\x03", 1),  # ctrl-c — cooperative cancel, not a kill
    (b"\x15", 1),  # ctrl-u — clear the line
    (b"\x0e", 1),  # ctrl-n — newline in the planning composer
    # Text a Latin-1 assumption breaks on: combining marks, right-to-left, wide
    # CJK, an emoji with a zero-width joiner. Rich measures cell width, and a
    # width miscount is how a panel border ends up one column short.
    ("é́".encode(), 2),
    ("مرحبا".encode(), 2),
    ("日本語テキスト".encode(), 2),
    ("👩‍💻".encode(), 2),
    # An oversized paste. `paste-icrnl-trap` is a real bug this repo has had:
    # a paste that stops early leaks the remainder as keystrokes.
    (b"\x1b[200~" + b"x" * 4000 + b"\x1b[201~", 1),
    # Ordinary letters, which are also the mode hotkeys.
    *((bytes([c]), 2) for c in b"abcdefghijklmnoprstuvwxyz"),
    (b"q", 1),  # rare: it quits
)

_TRACEBACK = "Traceback (most recent call last):"
# Two shapes, because the two things we parse write frames differently: a
# traceback says `line 12, in func`, a faulthandler dump says `line 12 in func`.
_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+),? in (\S+)')
# …and in opposite orders. A traceback is outermost-first; a dump announces
# itself "most recent call first". Everything downstream reads `frames[-1]` as
# the deepest frame, so one of the two has to be turned around here.
_DUMP_MARKER = "(most recent call first)"


def key_sequence(seed: int, steps: int) -> list[bytes]:
    """The exact keystrokes for a seed. Pure, so a find can be replayed."""
    rng = random.Random(seed)  # noqa: S311 — reproducibility is the point; nothing here is a secret
    keys, weights = zip(*_KEYS)
    return rng.choices(keys, weights=weights, k=steps)


def traceback_frames(text: str) -> list[tuple[str, int, str]]:
    """(repo-relative file, line, function) for every frame under our tree.

    Frames outside the repository — `rich`, `anthropic`, the standard library —
    are dropped: a crash is ours where our code is, and the deepest frame we
    own is the one a builder has to look at.
    """
    frames = []
    for path, line, func in _FRAME_RE.findall(text):
        try:
            frames.append((Path(path).resolve().relative_to(REPO_ROOT).as_posix(), int(line), func))
        except ValueError:
            continue
    return frames[::-1] if _DUMP_MARKER in text else frames


def exception_line(text: str) -> str:
    """The `SomeError: message` line closing the last traceback in the output."""
    tail = text.rsplit(_TRACEBACK, 1)[-1]
    for line in reversed([ln.rstrip() for ln in tail.splitlines() if ln.strip()]):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt|Warning)\b", line):
            return line
    return ""


def _strip_ansi(raw: bytes) -> str:
    return _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))


def _child_env(home: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "HOME": str(home),
        "TERM": "xterm-256color",
        "LOG_LEVEL": "WARNING",
        "ANTHROPIC_API_KEY": "test-key-fuzz-only",
        "YEABOI_NO_TUNNEL": "1",
        # A hang has no traceback, so it has no frame, so it cannot be
        # attributed to a charter — which made every hang unfileable. With
        # this on, `SIGABRT` makes the interpreter dump every thread's stack
        # before it dies, and a wedged screen names the line it is wedged on.
        "PYTHONFAULTHANDLER": "1",
        # Nothing listens on 9. A mode that ignores --dry-run fails to connect
        # instead of billing somebody, and the failure lands on the same error
        # path a user with no network takes.
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
        # A random `o` must not launch a browser at the operator's desk.
        "BROWSER": "true",
        # `music.py` decides by `shutil.which("ffplay")`, so a shim ahead of the
        # real one on PATH is the only thing that reliably keeps a fuzzed
        # keystroke from starting a radio stream on somebody's speakers. The
        # app's own "a missing or broken ffplay leaves music off" path is what
        # runs instead, which is a path worth fuzzing anyway.
        "PATH": f"{home / 'shims'}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    # Set, not popped: unset now falls through to the worktree's
    # .worktree.env marker, which would land this run in the shared
    # worktree tree instead of the temp HOME below it.
    env["YEABOI_HOME"] = str(home / ".yeaboi")
    env.pop("ANTHROPIC_BASE_URL", None)
    return env


def _dump_stacks(proc: subprocess.Popen, master_fd: int, tail: _Tail) -> str:
    """Ask a wedged process where it is, by aborting it under `faulthandler`.

    This is the only way a hang becomes a *find*. A traceback carries frames
    and a hang carries none, so before this every hang was reported as
    "no frame in our tree" and quietly dropped — the lens could see the
    failure and never say whose it was. `SIGABRT` with `PYTHONFAULTHANDLER=1`
    prints every thread's stack to stderr, which arrives on the same pty.

    It goes to the process and not the group: we want one interpreter's dump,
    not to abort every child it started.
    """
    before = tail.text()
    try:
        proc.send_signal(signal.SIGABRT)
    except (OSError, ProcessLookupError):
        return ""
    _drain(master_fd, 3.0, tail)
    after = tail.text()
    return after[len(before) :] if after.startswith(before) else after


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the whole session, not just the process we started.

    `start_new_session=True` is what gives the child its own controlling
    terminal, and it is also what makes `proc.kill()` insufficient: the group
    survives, gets reparented to init, and keeps repainting a screen nobody is
    reading at 100% of a core, forever. A `Ctrl-C` at the wrong moment used to
    leave one of those behind on the operator's machine every time.
    """
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, AttributeError):
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _spawn(home: Path):
    import fcntl
    import termios

    (home / ".yeaboi").mkdir(parents=True, exist_ok=True)
    (home / ".yeaboi" / ".env").write_text("ANTHROPIC_API_KEY=test-key-fuzz-only\n")
    shims = home / "shims"
    shims.mkdir(exist_ok=True)
    for binary in ("ffplay", "open", "xdg-open", "cloudflared"):
        shim = shims / binary
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
    master_fd, slave_fd = os.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
    # Non-blocking on the master end, so an oversized paste into a full input
    # buffer returns a partial count instead of parking the fuzzer forever.
    fcntl.fcntl(master_fd, fcntl.F_SETFL, fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "yeaboi.cli", "--dry-run"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=_child_env(home),
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave_fd)
    return proc, master_fd


# How much of the pty tail is kept. The screen repaints at 60 FPS, so a
# twenty-second run paints tens of megabytes of mostly-identical frames.
# Accumulating that with `buf += chunk` is quadratic and wedges the fuzzer long
# before it wedges the app — the same trap `test_tui_smoke.py` documents, which
# this file managed to walk straight back into.
_TAIL_BYTES = 1 << 20


class _Tail:
    """A bounded view of the pty, plus how much really came through it."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        self._buf += chunk
        if len(self._buf) > _TAIL_BYTES:
            del self._buf[: len(self._buf) - _TAIL_BYTES]

    def text(self) -> str:
        return _strip_ansi(bytes(self._buf))


def _write(master_fd: int, key: bytes, budget: float = 1.0) -> bool:
    """Send one keystroke, or give up.

    Two ways this blocks forever, and the second is the one that bit. A pty's
    input buffer fills whenever the child stops reading — which is exactly what
    a wedged screen looks like — so a bare `os.write` waits on a hang instead of
    reporting one, for as long as anybody lets it. Selecting for
    write-readiness fixes that only for a *small* write: `select` says writable
    when *some* space is free, and the 4000-byte paste in the alphabet is
    larger than that space. The master end is `O_NONBLOCK`, so the partial
    write comes back as a count and this loops on the remainder under one
    deadline.
    """
    view = memoryview(key)
    deadline = time.monotonic() + budget
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        _, ready, _ = select.select([], [master_fd], [], remaining)
        if not ready:
            return False
        try:
            view = view[os.write(master_fd, view) :]
        except BlockingIOError:
            continue
        except OSError:
            return False
    return True


def _drain(master_fd: int, budget: float, tail: _Tail) -> int:
    """Read whatever the pty has to give within `budget` seconds; return the byte count."""
    read = 0
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master_fd], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        try:
            chunk = os.read(master_fd, 65536)
        except BlockingIOError:
            # `O_NONBLOCK` (see `_write`) makes a raced read say "not yet"
            # rather than wait. Reading that as EOF would end the drain early
            # and report a screen as blank that had simply not painted yet.
            continue
        except OSError:
            break
        if not chunk:
            break
        tail.feed(chunk)
        read += len(chunk)
    return read


def _logged_tracebacks(home: Path) -> str:
    """Tracebacks the app swallowed into its own logs.

    The render loop catches broadly and logs, so the strongest crashes never
    reach the terminal at all. `HOME` is a temp directory, so every log the run
    produced is here and nowhere near the operator's own tree.
    """
    found = []
    for log in sorted((home / ".yeaboi" / "logs").rglob("*.log")):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _TRACEBACK in text:
            found.append(f"--- {log.name} ---\n{text[text.index(_TRACEBACK) :][:8000]}")
    return "\n".join(found)


def fuzz_once(seed: int, *, steps: int, hang_seconds: float, boot_seconds: float, wall_seconds: float) -> dict:
    """One process, one seed. Returns a verdict and everything needed to replay it."""
    home = Path(tempfile.mkdtemp(prefix=f"yeaboi-fuzz-{seed}-"))
    keys = key_sequence(seed, steps)
    sent: list[str] = []
    proc, master_fd = _spawn(home)
    tail = _Tail()
    verdict = "ok"
    dump = ""
    try:
        _drain(master_fd, boot_seconds, tail)
        # Past the Humans/Agents landing split and into the mode menu, so the
        # random half of the run starts somewhere the smoke test does not end.
        _write(master_fd, b"\r")
        sent.append("\\r")
        _drain(master_fd, 2.0, tail)

        # A hard wall clock as well as the per-key budgets. Every timeout below
        # is a bound on one wait; this is the bound on all of them together, so
        # a seed can never become the reason a sweep does not finish.
        deadline = time.monotonic() + wall_seconds
        for key in keys:
            if proc.poll() is not None or time.monotonic() > deadline:
                break
            if not _write(master_fd, key):
                # One second of a full input buffer is not a wedge — the render
                # loop reads on a timeout and a heavy repaint can outrun it.
                # Drain what it has painted and ask again with the full hang
                # budget; only a screen that still will not take a keystroke
                # after that is one that stopped reading.
                _drain(master_fd, 0.5, tail)
                if not _write(master_fd, key, budget=hang_seconds):
                    verdict = "hang" if proc.poll() is None else verdict
                    break

            sent.append(key.decode("utf-8", errors="backslashreplace"))
            _drain(master_fd, 0.05, tail)
            if _TRACEBACK in tail.text():
                verdict = "traceback"
                break

        if verdict == "ok" and proc.poll() is None:
            # A screen that stopped repainting has either finished or wedged.
            # Anything longer than this on a 60 FPS loop is the second.
            if _drain(master_fd, hang_seconds, tail) == 0:
                verdict = "hang"
        if verdict == "hang" and proc.poll() is None:
            dump = _dump_stacks(proc, master_fd, tail)
        _drain(master_fd, 1.0, tail)
    finally:
        _terminate(proc)
        os.close(master_fd)

    text = tail.text()
    logged = _logged_tracebacks(home)
    shutil.rmtree(home, ignore_errors=True)

    if _TRACEBACK in text or logged:
        verdict = "traceback"
    elif verdict == "ok" and proc.returncode not in (0, None, -9):
        verdict = "exit"

    excerpt = ""
    if verdict == "traceback":
        source = logged or text
        excerpt = source[source.index(_TRACEBACK) :][:4000] if _TRACEBACK in source else source[-4000:]
    elif verdict == "hang":
        # The dump, not the screen: the last 2000 columns of a repainting
        # panel say nothing about where it stopped reading.
        excerpt = dump[-4000:] if dump else text[-2000:]
    elif verdict != "ok":
        excerpt = text[-2000:]

    return {
        "seed": seed,
        "steps": steps,
        "verdict": verdict,
        "returncode": proc.returncode,
        # Depth, reported because `ok` is ambiguous without it: a run that quit
        # on its third keystroke also finds nothing, and a fuzzer whose silence
        # cannot be told apart from a pass is not evidence of anything. `sent`
        # against `steps` says how far the run actually got.
        "keys_sent": len(sent),
        "output_bytes": tail.total,
        "keys": sent,
        "frames": traceback_frames(excerpt),
        "exception": exception_line(excerpt),
        "excerpt": excerpt,
    }


def fuzz(
    seeds: list[int], *, steps: int, hang_seconds: float, boot_seconds: float, wall_seconds: float = 90.0
) -> list[dict]:
    return [
        fuzz_once(s, steps=steps, hang_seconds=hang_seconds, boot_seconds=boot_seconds, wall_seconds=wall_seconds)
        for s in seeds
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=6, help="how many seeds to run (0, 1, 2, …)")
    parser.add_argument("--seed", type=int, help="run exactly one seed — how a reported find is reproduced")
    parser.add_argument("--steps", type=int, default=120, help="keystrokes per seed")
    parser.add_argument("--hang-seconds", type=float, default=6.0, help="silence that counts as wedged")
    parser.add_argument("--boot-seconds", type=float, default=12.0, help="budget for the splash and first paint")
    parser.add_argument("--wall-seconds", type=float, default=90.0, help="hard ceiling on one seed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        print("tui_fuzz needs a pty, and so does the TUI it drives — POSIX only.", file=sys.stderr)
        return 0

    seeds = [args.seed] if args.seed is not None else list(range(args.seeds))
    results = fuzz(
        seeds,
        steps=args.steps,
        hang_seconds=args.hang_seconds,
        boot_seconds=args.boot_seconds,
        wall_seconds=args.wall_seconds,
    )

    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    for r in results:
        print(
            f"seed {r['seed']:>4}  {r['keys_sent']:>4} keys sent  "
            f"{r['output_bytes'] // 1024:>5} KiB painted  {r['verdict']}"
        )
        if r["verdict"] != "ok":
            if r["exception"]:
                print(f"    {r['exception']}")
            for path, line, func in r["frames"][-3:]:
                print(f"    {path}:{line} in {func}")
            print(f"    replay: uv run python scripts/tui_fuzz.py --seed {r['seed']} --steps {r['steps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
