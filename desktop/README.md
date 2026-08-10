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

**Not built.** The prerequisites are checked and recorded here so the next pass
starts from facts rather than from optimism:

- Rust is present (`rustc 1.94.0`, Homebrew) — no `rustup`, which matters only
  if a specific toolchain is ever pinned.
- `@tauri-apps/cli` 2.x exists on npm and is not installed.
- The webview on macOS is WKWebView, which the app's CSP already suits: the
  shell is served with `connect-src 'self'`, and a sidecar on `127.0.0.1` is
  same-origin to the page it served.

## What to do first, in order

1. Teach `yeaboi app` `--port 0` and a `listening on http://127.0.0.1:<port>`
   line on stdout. Nothing else can be built until the shell can find the server.
2. `npm create tauri-app` in this directory, targeting the existing server
   rather than a static `dist/`.
3. Sidecar lifecycle: spawn on ready, kill on exit, and a test that the process
   is gone afterwards.
4. Packaging: bundle a Python runtime, or declare a dependency on one.

Steps 1 and 3 are the ones with real failure modes; 2 is scaffolding and 4 is
the long tail.
