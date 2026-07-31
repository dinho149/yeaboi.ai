#!/usr/bin/env python3
"""Record the README/docs TUI demos from a *real* iTerm2 window.

Why this exists rather than ``asciinema``/``agg``/VHS
-----------------------------------------------------
Those tools re-render the session with their own font engine. That is fine for a
plain shell transcript and wrong for us: the TUI leans on iTerm2's rendering —
ligatures, the Nerd Font glyphs in the prompt, true-colour panel borders, and
the block-font ASCII titles. Recording the actual window is the only way the GIF
looks like what a user sees.

The cost of recording a real window is that it is normally *performed by hand*,
which is exactly why ``docs/demo.gif`` sat untouched from PR #16 while the TUI
moved on underneath it. So the input here is scripted end to end:

* **Keyboard and mouse** come from ``cliclick``, which posts real Quartz HID
  events. iTerm2 cannot tell them from a human, so mouse reporting produces the
  same SGR sequences that :mod:`yeaboi.ui.shared._input` already decodes into
  ``click:<x>:<y>``. Cursor travel uses cliclick's own easing (``-e``) so the
  pointer arcs the way a hand moves instead of teleporting.
* **Window setup** is AppleScript. iTerm2's Python API would also work but it is
  off by default behind a GUI toggle (Preferences → General → Magic), and this
  script is meant to run without one.
* **Synchronisation** reads the session back with AppleScript ``contents``
  rather than sleeping a guessed number of seconds. A step can therefore say
  "wait until the word Epics appears", which survives the pipeline running at a
  different speed on a different machine.

Because every step is data, re-recording after a UI change is ``make demo`` and
not an afternoon of retakes.

Coordinates
-----------
Clicks are authored in **terminal cells**, not pixels — ``Click(col=12, row=4)``
means the same thing whether the window is on a Retina display or not. The
cell→pixel conversion needs to know where the grid starts inside the window,
which depends on the profile's padding and the title bar. That offset is
measured once by ``--calibrate`` and cached; see :func:`calibrate`.

Permissions (both are one-time, both are GUI prompts)
-----------------------------------------------------
* **Accessibility** for the terminal running this script — lets ``cliclick``
  post events. Without it the cursor silently does not move.
* **Screen Recording** for the same process — lets ``screencapture``/``ffmpeg``
  see the display. Without it capture fails *silently*: ``screencapture`` writes
  no file at all and ``ffmpeg`` hangs waiting for frames that never arrive.
  :func:`preflight` checks for this up front so a run cannot fail halfway.

Usage::

    uv run python scripts/record_demo.py --list
    uv run python scripts/record_demo.py --calibrate
    uv run python scripts/record_demo.py planning
    uv run python scripts/record_demo.py --all
    uv run python scripts/record_demo.py planning --dry-run   # print, do nothing

**Not a build step.** Nothing in ``make test`` or ``make build`` runs this; the
outputs are committed. It lives here so the next person can regenerate the demo
instead of reverse-engineering how the last one was made.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"

# Machine-specific, so it does not belong in git. Sits under the same root the
# app uses for its own state (see src/yeaboi/paths.py) rather than inventing a
# second dotfile location.
CALIBRATION_PATH = Path.home() / ".yeaboi" / "demo-calibration.json"

# The iTerm2 profile the recording runs in. Keeping this separate from the
# user's daily profile is what makes the look reproducible — font size, colours,
# and padding are pinned by the profile rather than by whatever the window
# happened to be set to. See scripts/README-demo.md for the settings.
DEMO_PROFILE = "Demo"

# Window geometry. 1600x900 downscales cleanly to the 800px the README renders
# at, and 16:9 reads far better inline than the near-square 1833x1456 the old
# demo.gif used.
WINDOW_W = 1600
WINDOW_H = 900
WINDOW_X = 80
WINDOW_Y = 80

# cliclick's easing factor. 0 teleports; higher is slower and more hand-like,
# with the duration scaling by distance. 30 reads as deliberate without looking
# sluggish over the ~700px hops between panels.
EASING = 30

# Capture framerate. The TUI redraws in discrete chunks so 30 is plenty smooth,
# and every extra frame is paid for again in the GIF.
FPS = 30


# ---------------------------------------------------------------------------
# Steps — the vocabulary a scenario is written in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Type:
    """Type literal text, as though at the keyboard."""

    text: str


@dataclass(frozen=True)
class Key:
    """Press a named key. See ``cliclick -h`` for the full list."""

    name: str
    times: int = 1


@dataclass(frozen=True)
class Move:
    """Ease the pointer to the centre of a terminal cell without clicking."""

    col: int
    row: int


@dataclass(frozen=True)
class Click:
    """Ease the pointer to a cell and click it."""

    col: int
    row: int


@dataclass(frozen=True)
class Scroll:
    """Scroll the wheel. Positive is up, negative is down."""

    amount: int


@dataclass(frozen=True)
class Wait:
    """Hold still. Use for beats the viewer needs, not for synchronisation."""

    seconds: float


@dataclass(frozen=True)
class WaitFor:
    """Block until ``text`` appears on screen, or ``timeout`` elapses.

    This is the one that keeps a tape working. Sleeps encode how fast the
    machine that recorded it happened to be; this encodes what the TUI is
    actually meant to show.
    """

    text: str
    timeout: float = 30.0


Step = Type | Key | Move | Click | Scroll | Wait | WaitFor


@dataclass(frozen=True)
class Scenario:
    """One recording: a command to run and the steps to perform against it."""

    name: str
    title: str
    output: str
    command: str
    steps: tuple[Step, ...] = field(default_factory=tuple)
    # Trailing hold so the GIF does not snap back to frame 0 the instant the
    # last action lands.
    tail: float = 2.0


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
#
# Every scenario runs against `yeaboi --dry-run` (mock data, fake delays, no LLM
# calls) so the timings are stable and no API key is needed. WaitFor strings are
# matched against the visible screen, so they must be text the TUI actually
# paints — if a screen is reworded, the tape fails loudly at that step instead of
# silently recording the wrong thing.

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="planning",
        title="Project description to sprint plan",
        output="demo.gif",
        command="yeaboi --dry-run",
        steps=(
            WaitFor("Planning"),
            Wait(1.5),
            # Mouse: pick the Planning card directly rather than arrowing to it.
            # The TUI has no hover state (motion events are consumed in
            # _input.py), so the click landing is the only feedback the viewer
            # gets — hence the beat before and after.
            Click(col=40, row=14),
            Wait(1.0),
            WaitFor("description"),
            Wait(0.8),
            Type("A mobile app for tracking home energy usage"),
            Wait(1.0),
            Key("return"),
            WaitFor("Epics", timeout=60),
            Wait(2.0),
            Scroll(-3),
            Wait(1.5),
            Key("return"),
            WaitFor("Stories", timeout=60),
            Wait(2.5),
            Key("return"),
            WaitFor("Sprint", timeout=60),
            Wait(3.0),
        ),
    ),
    Scenario(
        name="tour",
        title="Mode select — six modes, one command",
        output="demo-tour.gif",
        command="yeaboi --dry-run",
        steps=(
            WaitFor("Planning"),
            Wait(1.5),
            # Slow sweep across the cards. Pure cursor travel with no clicks:
            # this is the one place the mouse movement itself is the content.
            Move(col=20, row=14),
            Wait(0.6),
            Move(col=40, row=14),
            Wait(0.6),
            Move(col=60, row=14),
            Wait(0.6),
            Move(col=80, row=14),
            Wait(0.8),
            Key("arrow-right", times=2),
            Wait(1.2),
            Key("arrow-left"),
            Wait(1.5),
        ),
    ),
    Scenario(
        name="standup",
        title="Daily standup",
        output="demo-standup.gif",
        command="yeaboi --dry-run",
        steps=(
            WaitFor("Standup"),
            Wait(1.2),
            Key("arrow-right", times=2),
            Wait(1.0),
            Key("return"),
            WaitFor("Standup", timeout=45),
            Wait(3.0),
        ),
    ),
    Scenario(
        name="retro",
        title="Collaborative retro",
        output="demo-retro.gif",
        command="yeaboi --dry-run",
        steps=(
            WaitFor("Retro"),
            Wait(1.2),
            Key("arrow-right", times=3),
            Wait(1.0),
            Key("return"),
            WaitFor("Retro", timeout=45),
            Wait(3.0),
        ),
    ),
    Scenario(
        name="reporting",
        title="Reporting deck",
        output="demo-reporting.gif",
        command="yeaboi --dry-run",
        steps=(
            WaitFor("Reporting"),
            Wait(1.2),
            Key("arrow-right", times=6),
            Wait(1.0),
            Key("return"),
            WaitFor("Reporting", timeout=45),
            Wait(3.0),
        ),
    ),
)

SCENARIOS_BY_NAME = {s.name: s for s in SCENARIOS}


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    logger.debug("run: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _osascript(script: str) -> str:
    """Run AppleScript and return stdout, raising with stderr on failure."""
    proc = _run(["osascript", "-e", script])
    if proc.returncode != 0:
        raise RuntimeError(f"osascript failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Geometry — terminal cells to screen pixels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Geometry:
    """Where the character grid sits on screen, in pixels.

    ``origin_x``/``origin_y`` are the top-left of cell (1, 1); ``cell_w``/
    ``cell_h`` are one character. Everything else is arithmetic.
    """

    origin_x: float
    origin_y: float
    cell_w: float
    cell_h: float

    def pixel(self, col: int, row: int) -> tuple[int, int]:
        """Centre of the 1-based cell ``(col, row)``, matching _click.py's coords."""
        x = self.origin_x + (col - 0.5) * self.cell_w
        y = self.origin_y + (row - 0.5) * self.cell_h
        return round(x), round(y)

    def to_json(self) -> dict[str, float]:
        return {
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "cell_w": self.cell_w,
            "cell_h": self.cell_h,
        }

    @classmethod
    def from_json(cls, data: dict[str, float]) -> Geometry:
        return cls(**{k: float(data[k]) for k in ("origin_x", "origin_y", "cell_w", "cell_h")})


def load_calibration() -> Geometry | None:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        return Geometry.from_json(json.loads(CALIBRATION_PATH.read_text()))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("ignoring unreadable calibration at %s: %s", CALIBRATION_PATH, exc)
        return None


def save_calibration(geometry: Geometry) -> None:
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(geometry.to_json(), indent=2) + "\n")
    logger.info("wrote calibration to %s", CALIBRATION_PATH)


def derive_geometry(bounds: tuple[int, int, int, int], cols: int, rows: int) -> Geometry:
    """Best-effort cell geometry from the window frame and grid size.

    iTerm2's window bounds include the title bar and the profile's padding, and
    AppleScript exposes neither. So this assumes symmetric horizontal padding and
    attributes all leftover vertical space to the title bar — close enough to
    land inside a cell for most profiles, and ``--calibrate`` exists to correct
    it when it is not.
    """
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top

    # A monospace cell is roughly 0.6 of its height; solve for the pair that
    # fills the width exactly, then let the vertical leftover be chrome.
    cell_w = width / cols
    cell_h = cell_w / 0.6
    used_h = cell_h * rows
    chrome = max(height - used_h, 0)

    return Geometry(origin_x=float(left), origin_y=float(top + chrome), cell_w=cell_w, cell_h=cell_h)


# ---------------------------------------------------------------------------
# iTerm2 window control
# ---------------------------------------------------------------------------


def _profile_exists(name: str) -> bool:
    try:
        out = _osascript('tell application "iTerm" to get name of every profile')
    except RuntimeError:
        return False
    return name in {p.strip() for p in out.split(",")}


def open_window(command: str, profile: str | None) -> str:
    """Open a dedicated iTerm2 window, run ``command``, return its window id."""
    use_profile = f'profile "{profile}"' if profile else "default profile"
    script = f"""
    tell application "iTerm"
      activate
      set w to (create window with {use_profile})
      set bounds of w to {{{WINDOW_X}, {WINDOW_Y}, {WINDOW_X + WINDOW_W}, {WINDOW_Y + WINDOW_H}}}
      tell current session of w
        write text "clear && {command}"
      end tell
      return id of w
    end tell
    """
    window_id = _osascript(script)
    logger.info("opened iTerm2 window %s running %r", window_id, command)
    return window_id


def window_bounds(window_id: str) -> tuple[int, int, int, int]:
    out = _osascript(f'tell application "iTerm" to get bounds of window id "{window_id}"')
    parts = [int(p.strip()) for p in out.split(",")]
    if len(parts) != 4:
        raise RuntimeError(f"unexpected bounds from iTerm2: {out!r}")
    return parts[0], parts[1], parts[2], parts[3]


def window_grid(window_id: str) -> tuple[int, int]:
    """Grid size in cells.

    AppleScript's ``columns``/``rows`` on a session report 1 until the session
    has settled, so this asks the shell instead — ``stty size`` is authoritative
    and always available.
    """
    marker = "YEABOI_GRID"
    _osascript(
        f'tell application "iTerm" to tell current session of window id "{window_id}" '
        f'to write text "printf \'{marker} %s\\n\' \\"$(stty size)\\""'
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        text = read_screen(window_id)
        for line in text.splitlines():
            if line.startswith(marker) and len(line.split()) >= 3:
                _, rows, cols = line.split()[:3]
                if rows.isdigit() and cols.isdigit():
                    return int(cols), int(rows)
        time.sleep(0.2)
    raise RuntimeError("could not determine terminal grid size")


def read_screen(window_id: str) -> str:
    """Visible text of the window's current session."""
    return _osascript(f'tell application "iTerm" to get contents of current session of window id "{window_id}"')


def close_window(window_id: str) -> None:
    try:
        _osascript(f'tell application "iTerm" to close window id "{window_id}"')
    except RuntimeError as exc:  # already gone, or the user closed it
        logger.warning("could not close window %s: %s", window_id, exc)


# ---------------------------------------------------------------------------
# Input choreography
# ---------------------------------------------------------------------------


def perform(step: Step, geometry: Geometry, window_id: str, dry_run: bool) -> None:
    """Execute one step. With ``dry_run`` the action is printed, never posted."""
    cmds: list[str] = []
    flags: list[str] = []

    match step:
        case Type(text=text):
            cmds = [f"t:{text}"]
        case Key(name=name, times=times):
            cmds = [f"kp:{name}"] * times
        case Move(col=col, row=row):
            x, y = geometry.pixel(col, row)
            flags = ["-e", str(EASING)]
            cmds = [f"m:{x},{y}"]
        case Click(col=col, row=row):
            x, y = geometry.pixel(col, row)
            flags = ["-e", str(EASING)]
            # Move first, then click as a separate event: a bare c: teleports,
            # and the whole point is that the viewer sees the pointer travel.
            cmds = [f"m:{x},{y}", "w:150", f"c:{x},{y}"]
        case Scroll(amount=amount):
            # cliclick has no wheel command; iTerm2 maps arrow keys inside the
            # TUI's scrollable panels, which is what the viewer would use anyway.
            key = "arrow-up" if amount > 0 else "arrow-down"
            cmds = [f"kp:{key}"] * abs(amount)
        case Wait(seconds=seconds):
            if dry_run:
                print(f"    wait {seconds}s")
            else:
                time.sleep(seconds)
            return
        case WaitFor(text=text, timeout=timeout):
            if dry_run:
                print(f"    wait for {text!r} (timeout {timeout}s)")
                return
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if text in read_screen(window_id):
                    logger.debug("matched %r", text)
                    return
                time.sleep(0.3)
            raise TimeoutError(
                f"timed out after {timeout}s waiting for {text!r}. "
                "The TUI may have been reworded — update the scenario's WaitFor."
            )
        case _:  # pragma: no cover - exhaustive over Step
            raise TypeError(f"unknown step: {step!r}")

    if dry_run:
        print(f"    cliclick {' '.join(flags + cmds)}")
        return

    proc = _run(["cliclick", *flags, *cmds])
    if proc.returncode != 0:
        raise RuntimeError(f"cliclick failed: {proc.stderr.strip()}")


# ---------------------------------------------------------------------------
# Capture and encode
# ---------------------------------------------------------------------------


def start_capture(path: Path, bounds: tuple[int, int, int, int]) -> subprocess.Popen:
    """Begin recording the window region. Returns the process to stop later."""
    left, top, right, bottom = bounds
    region = f"{left},{top},{right - left},{bottom - top}"
    # -v records video, -R limits to a region, -x silences the shutter sound.
    return subprocess.Popen(
        ["screencapture", "-v", "-x", "-R", region, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_capture(proc: subprocess.Popen) -> None:
    """Stop screencapture cleanly so it finalises the MOV container.

    It stops on any stdin input; killing it instead leaves an unplayable file.
    """
    try:
        if proc.stdin:
            proc.stdin.write(b"\n")
            proc.stdin.flush()
            proc.stdin.close()
        proc.wait(timeout=20)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        proc.terminate()
        proc.wait(timeout=10)


def encode_gif(source: Path, dest: Path, width: int = 1600) -> None:
    """Convert the capture to a GIF, preferring gifski.

    gifski does per-frame palettes and temporal dithering, which on terminal
    output is the difference between clean text and visible colour banding.
    ffmpeg's palettegen is the fallback so this works with nothing installed.
    """
    if shutil.which("gifski"):
        proc = _run(
            [
                "gifski",
                "--fps",
                str(FPS),
                "--width",
                str(width),
                "--quality",
                "90",
                "-o",
                str(dest),
                str(source),
            ]
        )
        if proc.returncode == 0:
            return
        logger.warning("gifski failed (%s), falling back to ffmpeg", proc.stderr.strip()[:200])

    palette = source.with_suffix(".png")
    filters = f"fps={FPS},scale={width}:-1:flags=lanczos"
    gen = _run(["ffmpeg", "-v", "error", "-i", str(source), "-vf", f"{filters},palettegen", "-y", str(palette)])
    if gen.returncode != 0:
        raise RuntimeError(f"ffmpeg palettegen failed: {gen.stderr.strip()}")
    use = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-i",
            str(palette),
            "-lavfi",
            f"{filters}[x];[x][1:v]paletteuse",
            "-y",
            str(dest),
        ]
    )
    palette.unlink(missing_ok=True)
    if use.returncode != 0:
        raise RuntimeError(f"ffmpeg paletteuse failed: {use.stderr.strip()}")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(require_capture: bool) -> list[str]:
    """Return a list of problems that would make a run fail.

    Both permissions fail *silently* at the point of use, which is how you end
    up with a green run and a zero-byte GIF. Checking here turns that into a
    message before anything moves.
    """
    problems: list[str] = []

    if sys.platform != "darwin":
        problems.append("this script is macOS-only (iTerm2 + cliclick + screencapture)")
        return problems

    if not shutil.which("cliclick"):
        problems.append("cliclick is not installed — run: brew install cliclick")
    else:
        # Accessibility check: read the pointer, nudge it, read it back, restore.
        before = _run(["cliclick", "p"]).stdout.strip()
        if before:
            x, y = (int(v) for v in before.split(","))
            _run(["cliclick", f"m:{x + 7},{y + 7}"])
            after = _run(["cliclick", "p"]).stdout.strip()
            _run(["cliclick", f"m:{x},{y}"])
            if after == before:
                problems.append(
                    "cliclick cannot move the pointer — grant Accessibility to your terminal "
                    "in System Settings → Privacy & Security → Accessibility"
                )

    if not Path("/Applications/iTerm.app").exists():
        problems.append("iTerm2 is not installed at /Applications/iTerm.app")

    if require_capture:
        if not shutil.which("screencapture"):
            problems.append("screencapture is missing (unexpected on macOS)")
        else:
            # A 2-second capture of a tiny region. Screen Recording denial does
            # not raise — screencapture simply writes nothing — so the file's
            # existence is the only reliable signal.
            with tempfile.TemporaryDirectory() as tmp:
                probe = Path(tmp) / "capture-probe.mov"
                _run(["screencapture", "-v", "-x", "-V", "2", "-R", "0,0,200,150", str(probe)], timeout=25)
                if not probe.exists() or probe.stat().st_size == 0:
                    problems.append(
                        "screen capture produced no file — grant Screen Recording to your terminal "
                        "in System Settings → Privacy & Security → Screen Recording, then "
                        "restart the terminal (the grant only applies to newly launched processes)"
                    )

        if not shutil.which("gifski") and not shutil.which("ffmpeg"):
            problems.append("neither gifski nor ffmpeg is installed — run: brew install gifski")

    return problems


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate(profile: str | None) -> int:
    """Measure cell geometry, then prove it by pointing at known cells.

    The derivation in :func:`derive_geometry` guesses the padding and title-bar
    height, because AppleScript will not tell us either. This opens a window,
    prints a ruler, and walks the pointer across four labelled cells so a human
    can confirm it lands on them. That is the one step of this pipeline that
    genuinely needs eyes.
    """
    window_id = open_window("clear", profile)
    try:
        time.sleep(1.5)
        bounds = window_bounds(window_id)
        cols, rows = window_grid(window_id)
        geometry = derive_geometry(bounds, cols, rows)

        print(f"window bounds : {bounds}")
        print(f"grid          : {cols} cols x {rows} rows")
        print(f"cell size     : {geometry.cell_w:.2f} x {geometry.cell_h:.2f} px")
        print(f"grid origin   : ({geometry.origin_x:.1f}, {geometry.origin_y:.1f})")
        print()

        # Paint targets the pointer should land on, then visit each one.
        _osascript(
            f'tell application "iTerm" to tell current session of window id "{window_id}" '
            f"to write text \"clear; printf '\\\\033[3;10HX  <- (10,3)\\\\n'; "
            f"printf '\\\\033[3;60HX  <- (60,3)\\\\n'; "
            f"printf '\\\\033[20;10HX  <- (10,20)\\\\n'; "
            f"printf '\\\\033[20;60HX  <- (60,20)\\\\n'\""
        )
        time.sleep(1.0)

        for col, row in ((10, 3), (60, 3), (10, 20), (60, 20)):
            x, y = geometry.pixel(col, row)
            print(f"  pointing at cell ({col},{row}) -> pixel ({x},{y})")
            _run(["cliclick", "-e", str(EASING), f"m:{x},{y}"])
            time.sleep(1.2)

        save_calibration(geometry)
        print()
        print("Saved. If the pointer missed the X marks, edit the origin/cell values in")
        print(f"  {CALIBRATION_PATH}")
        print("and re-run --calibrate to re-verify. Clicks are authored in cells, so this")
        print("only needs doing once per profile/font-size.")
        return 0
    finally:
        time.sleep(2.0)
        close_window(window_id)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record(scenario: Scenario, profile: str | None, dry_run: bool, keep_video: bool) -> int:
    print(f"\n▶ {scenario.name} — {scenario.title}")

    if dry_run:
        print(f"  command: {scenario.command}")
        for step in scenario.steps:
            print(f"  {type(step).__name__}")
            perform(step, Geometry(0, 0, 10, 20), "", dry_run=True)
        print(f"  → would write docs/{scenario.output}")
        return 0

    geometry = load_calibration()
    window_id = open_window(scenario.command, profile)
    video = DOCS_DIR / f".{scenario.name}.mov"
    capture: subprocess.Popen | None = None

    try:
        time.sleep(2.0)
        bounds = window_bounds(window_id)
        if geometry is None:
            cols, rows = window_grid(window_id)
            geometry = derive_geometry(bounds, cols, rows)
            logger.warning("no saved calibration — using derived geometry; run --calibrate for accurate clicks")
            # The grid probe typed a command into the session, so restart clean.
            _osascript(
                f'tell application "iTerm" to tell current session of window id "{window_id}" '
                f'to write text "clear && {scenario.command}"'
            )
            time.sleep(2.0)

        capture = start_capture(video, bounds)
        time.sleep(1.0)  # let the recorder reach steady state before acting

        for index, step in enumerate(scenario.steps, 1):
            logger.info("step %d/%d: %s", index, len(scenario.steps), type(step).__name__)
            perform(step, geometry, window_id, dry_run=False)

        time.sleep(scenario.tail)
    finally:
        if capture is not None:
            stop_capture(capture)
        close_window(window_id)

    if not video.exists() or video.stat().st_size == 0:
        print("  ✗ capture produced no video — see the Screen Recording note in --help", file=sys.stderr)
        return 1

    dest = DOCS_DIR / scenario.output
    encode_gif(video, dest)
    if not keep_video:
        video.unlink(missing_ok=True)

    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"  ✓ docs/{scenario.output}  {size_mb:.1f} MB")
    if size_mb > 8:
        print("    (over 8 MB — consider trimming the scenario or dropping FPS)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scenario", nargs="*", help="scenario name(s) to record")
    parser.add_argument("--all", action="store_true", help="record every scenario")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--calibrate", action="store_true", help="measure and verify cell geometry")
    parser.add_argument("--dry-run", action="store_true", help="print the choreography without performing it")
    parser.add_argument("--keep-video", action="store_true", help="keep the intermediate .mov")
    parser.add_argument("--profile", default=DEMO_PROFILE, help=f"iTerm2 profile (default: {DEMO_PROFILE})")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.list:
        print("Scenarios:")
        for scenario in SCENARIOS:
            print(f"  {scenario.name:<12} {scenario.title}  → docs/{scenario.output}")
        return 0

    profile: str | None = args.profile
    if profile and not _profile_exists(profile):
        logger.warning("iTerm2 profile %r not found — using the default profile", profile)
        logger.warning("see scripts/README-demo.md for the recommended Demo profile settings")
        profile = None

    if args.calibrate:
        problems = preflight(require_capture=False)
        if problems:
            for problem in problems:
                print(f"✗ {problem}", file=sys.stderr)
            return 1
        return calibrate(profile)

    if args.all:
        chosen = list(SCENARIOS)
    elif args.scenario:
        unknown = [n for n in args.scenario if n not in SCENARIOS_BY_NAME]
        if unknown:
            print(f"✗ unknown scenario(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"  known: {', '.join(SCENARIOS_BY_NAME)}", file=sys.stderr)
            return 1
        chosen = [SCENARIOS_BY_NAME[n] for n in args.scenario]
    else:
        parser.print_help()
        return 1

    if not args.dry_run:
        problems = preflight(require_capture=True)
        if problems:
            print("Cannot record:", file=sys.stderr)
            for problem in problems:
                print(f"  ✗ {problem}", file=sys.stderr)
            return 1
        print("⚠ Do not touch the keyboard or mouse while recording — cliclick drives both.")
        time.sleep(2.0)

    failures = 0
    for scenario in chosen:
        try:
            failures += record(scenario, profile, args.dry_run, args.keep_video)
        except (RuntimeError, TimeoutError) as exc:
            print(f"  ✗ {scenario.name}: {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
