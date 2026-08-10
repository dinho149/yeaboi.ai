# desktop

Milestone 5: yeaboi as a desktop application, via [Tauri](https://tauri.app).

## Why this is not "the bundles already run without a server"

That claim is true of the *exports* and it is false of the *app*. An exported
report is inert by policy — `ARTIFACT_CSP` sets `connect-src 'none'`, so a
written file physically cannot make a request, and double-clicking one works
with no server anywhere. The app bundle is the opposite: it is the first surface
allowed to talk to an origin, and everything it shows arrives from `/api/…`.

So a desktop build is not "point a webview at a file". It is **the Python server
running as a child process of the desktop app**, with the webview pointed at it.
That shape is standard for Tauri (a "sidecar"), and it is the honest amount of
work rather than the hoped-for amount.

## The shape

```
Tauri window  ──webview──▶  http://127.0.0.1:<port>
     │                              ▲
     └── spawns, owns, kills ───────┘
              `yeaboi app --port <port>`
```

Three things that make it non-trivial, all of them real:

1. **The port must not be guessed.** 5599 may be taken. The server has to bind
   `:0`, report the port it actually got, and the shell has to read it — which
   means `yeaboi app` needs a machine-readable "listening on" line.
2. **The child must die with the parent.** A sidecar that outlives a force-quit
   leaves a server holding the user's projects on a port they cannot see.
3. **Python has to be there.** Either the user's interpreter (fine for a dev
   build, wrong for a distributable) or a bundled one (PyInstaller/`uv`-built,
   which is the real packaging work).

## Status

**It compiles and its logic is tested. It has not been run as a window, and it
is not packaged.**

Done:

- `yeaboi app --port 0` prints `listening on <url>` on stdout — everything else
  depended on the shell being able to find a server whose port it did not pick.
- `src-tauri/` builds against Tauri 2 (`make desktop-check`).
- `sidecar.rs` owns the child process: finds the port by reading that line,
  bounds the wait so a server that dies at startup does not hang an empty
  window forever, and kills the child on `Drop` so it cannot outlive the window.
- `make desktop-test` — 4 Rust tests on the parsing, including that a
  non-`http` scheme is refused, since the value goes straight to the webview.

### It runs

Verified, not assumed:

- The shell starts, spawns the sidecar, and the sidecar creates its store.
- **The sidecar dies with the window.** Killed the shell, checked for an
  orphaned `yeaboi app` — none. That was the failure mode most worth proving,
  because a server outliving its window holds the user's projects on a port
  they cannot see or stop.
- The failure path reads properly: pointing `YEABOI_SERVER_CMD` at a nonexistent
  program produces a message naming the program and the variable, rather than
  "exited before starting".

### Running it against this checkout

The `yeaboi` on a developer's PATH is often an **older release with no `app`
command at all** — that was true on the machine this was built on (2.55.0 on
PATH, 3.4.0 in the repo). The shell would have run the wrong program and died
with an argparse error pointing nowhere useful. Two things fix that:

```bash
YEABOI_SERVER_CMD=desktop/yeaboi-dev make desktop-run
```

`desktop/yeaboi-dev` is a two-line shim that runs this checkout's interpreter,
and `Sidecar::start` preflights `<cmd> app --help` so an old CLI is reported as
"has no `app` command" rather than as a mysterious startup failure.

Not done, in the order it matters:

1. **Nobody has looked at the window's contents.** It opens and the server
   behind it answers; whether the app *renders* correctly inside WKWebView is
   unverified.
2. **Packaging a Python runtime.** `SERVER_COMMAND` is `"yeaboi"` from `PATH`,
   which is right for development and wrong for anything shipped — a
   distributable cannot assume the CLI is installed. This is the long tail:
   PyInstaller or a `uv`-built runtime, plus code signing and notarisation on
   macOS.
3. **Icons.** `icons/icon.png` is a 1×1 placeholder that exists so the build
   works.

## Prerequisites, checked rather than assumed

- `rustc 1.94.0` (Homebrew) — no `rustup`, which matters only if a toolchain is
  ever pinned.
- macOS WKWebView suits the app's existing CSP: the shell is served with
  `connect-src 'self'`, and a sidecar on `127.0.0.1` is same-origin to the page
  it served, so no second policy is declared in `tauri.conf.json`.
- `desktop/dist/` exists because Tauri requires a `frontendDist`. It is not a
  placeholder: it is the page shown if the window ever opens without its
  server, which is the one moment a blank rectangle would be least helpful.
