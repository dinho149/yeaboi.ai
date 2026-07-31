# Recording the TUI demos

`docs/demo.gif` and its siblings are recorded from a **real iTerm2 window** by
`scripts/record_demo.py`, driven entirely by script. Nothing is performed by hand.

Why a real window rather than `asciinema`/`agg`/VHS: those re-render the session
with their own font engine, and the TUI depends on iTerm2's — Nerd Font prompt
glyphs, true-colour panel borders, the block-font ASCII titles. Why scripted
rather than performed: the previous `demo.gif` sat unchanged from PR #16 while
the TUI moved on underneath it. A tape you can re-run is a demo that can't rot.

```bash
make demo-list            # what can be recorded
make demo-calibrate       # one-time, per profile/font-size
make demo                 # re-record docs/demo.gif (the README hero)
make demo-all             # every scenario
make demo-check           # print the choreography, perform nothing
```

## One-time setup

### 1. Install the tools

```bash
brew install cliclick gifski
```

`cliclick` posts real Quartz HID events — iTerm2 cannot tell them from a human,
so mouse reporting produces the same SGR sequences `_input.py` already decodes.
`gifski` does the GIF encoding (per-frame palettes and temporal dithering, which
on terminal text is the difference between clean glyphs and colour banding).
`ffmpeg` is used automatically if `gifski` is absent, at lower quality.

### 2. Grant two permissions

Both are GUI prompts, and **both fail silently** when missing, which is why
`record_demo.py` checks them up front instead of letting a run half-succeed.

| Permission | Grant to | Symptom when missing |
|---|---|---|
| **Accessibility** | your terminal app | pointer never moves; no error |
| **Screen Recording** | your terminal app | `screencapture` writes **no file at all** |

System Settings → Privacy & Security → {Accessibility, Screen Recording}.

> **Restart your terminal after granting Screen Recording.** The grant only
> applies to newly launched processes, so an already-running shell keeps failing
> silently even after the checkbox is ticked.

### 3. Create the `Demo` iTerm2 profile

Pinning the look in a profile is what makes the recording reproducible — font
size and padding are fixed by the profile rather than by whatever the window
happened to be. Preferences → Profiles → `+`, name it **Demo**:

| Setting | Value | Why |
|---|---|---|
| Font | your usual Nerd Font, **16–18pt** | large enough to stay legible at the README's 800px |
| Window columns × rows | 120 × 34 | fills 1600×900 at that font size |
| Background | `#0b0c0e` | matches `docs/banner.jpg` and the docs site |
| Transparency / blur | **off** | wrecks GIF quantisation |
| Show tab bar / status bar | **off** | chrome is wasted pixels |
| Cursor | blinking off, block | a blinking cursor costs frames for no information |
| Scrollback | anything | unused; the TUI is full-screen |

Without it the script falls back to your default profile and warns.

### 4. Calibrate

```bash
make demo-calibrate
```

Clicks are authored in **terminal cells** (`Click(col=40, row=14)`), not pixels,
so a scenario stays valid across displays. Converting cells to pixels needs the
grid's origin inside the window, which depends on profile padding and title-bar
height — AppleScript exposes neither, so it is measured once.

Calibrate opens a window, prints `X` marks at four known cells, and walks the
pointer to each. **Watch it.** If the pointer lands on the marks, you're done —
the result is cached in `~/.yeaboi/demo-calibration.json`. If it misses, edit
those numbers and re-run. This is the only step that needs human eyes.

Re-calibrate when you change the profile's font size or padding.

## Recording

```bash
make demo          # just docs/demo.gif
make demo-all      # all five scenarios
```

**Do not touch the keyboard or mouse while it runs.** `cliclick` types into the
frontmost application; stealing focus sends your demo's keystrokes elsewhere.
Each scenario opens its own window and closes it afterwards.

Runs against `yeaboi --dry-run` — mock data, fake delays, no LLM calls, no API
key. That's also what makes the timings stable enough to be worth committing.

## Writing a scenario

Scenarios live in `SCENARIOS` in `record_demo.py` and are plain data:

```python
Scenario(
    name="planning",
    title="Project description to sprint plan",
    output="demo.gif",
    command="yeaboi --dry-run",
    steps=(
        WaitFor("Planning"),      # block until the text is on screen
        Wait(1.5),                # a beat for the viewer
        Click(col=40, row=14),    # eased pointer travel, then click
        Type("A mobile app ..."), # type as though at the keyboard
        Key("return"),
        Move(col=20, row=14),     # travel with no click
        Scroll(-3),
    ),
)
```

Two rules worth internalising:

- **`WaitFor` for synchronisation, `Wait` for pacing.** `Wait` encodes how fast
  the recording machine happened to be. `WaitFor` reads the screen back over
  AppleScript and encodes what the TUI is actually meant to show, so the tape
  survives a slower machine or a slower pipeline.
- **`WaitFor` strings are real screen text.** Reword a screen and the tape fails
  loudly at that step, rather than silently recording the wrong thing. That is
  the drift alarm — treat a `WaitFor` timeout as "the demo is out of date", not
  as a flaky script.

Preview without performing anything:

```bash
uv run python scripts/record_demo.py planning --dry-run
```

## Optional polish

Neither is wired in, both are one-off passes over the output:

- **[KeyCastr](https://keycastr.com)** (`brew install --cask keycastr`) overlays
  keystrokes on screen. For a keyboard-driven TUI this is the single biggest
  legibility win — without it, screens change with no visible cause. It needs
  its own Accessibility grant.
- **[Screen Studio](https://screen.studio)** for zooms, click ripples, and
  captions. Note the trade-off: it's a GUI editor, so anything done there is
  *not* reproducible. Keep it for a one-off hero video, not for `docs/demo.gif`.

## Size budget

The README renders at `width="800"`, so 1600px wide is the 2× asset. The old
demo was 25 MB at 1833×1456 — every README view paid for it. A tuned 10–15s
scenario lands around 2–4 MB; the script warns above 8 MB.

Levers, in the order worth pulling: shorten the scenario, drop `FPS` from 30 to
20, then reduce the encode width.
