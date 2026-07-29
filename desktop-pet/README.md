# yeaboi duck — desktop pet 🦆

A little yeaboi duck that waddles along your macOS dock, chases your cursor,
hops when you poke it, and can be picked up and carried around. Same sprite and
animations as the mascot on [yeaboi.ai](https://yeaboi.ai) — reborn as a
transparent, always-on-top desktop overlay.

## Run it

```bash
cd desktop-pet
npm install     # pulls Electron (~one-time, largish download)
npm start
```

The duck appears at the bottom-center of your primary display. There's no
window chrome — control it from the **menubar duck icon** (Come here / Quit).

## What it does

- **Waddles** back and forth along the bottom edge, over the dock.
- **Flees** when your cursor gets close; **hops** with a yelp if you corner it
  or click it.
- **Drag it** anywhere — grab the duck and it follows the cursor, then drops
  back to the ground when released.
- **Chatters** the occasional one-liner in a speech bubble.

## How it works

One transparent, frameless, `alwaysOnTop` window is stretched across the whole
display (using `display.bounds`, not `workArea`, so it also covers the dock).
The window stays **click-through** (`setIgnoreMouseEvents(true, {forward:true})`)
so every app underneath keeps working — main polls the OS cursor position and
feeds it to the renderer so the duck can react to your mouse anywhere on screen.
When the cursor is over the duck's hitbox (or you're mid-drag), the renderer
asks main to make the window solid for that instant so the click/drag lands on
the duck, then it flips back to click-through the moment the cursor leaves.

| File | Role |
|------|------|
| `main.js` | Electron main: the overlay window, click-through toggle, cursor feed, menubar tray |
| `preload.js` | Locked-down `window.pet` bridge (contextIsolation on) |
| `index.html` / `pet.css` | The three-layer duck sprite + animations (ported from the landing mascot) |
| `pet.js` | Behavior: wander / flee / startle-hop / drag state machine |
| `assets/` | `duck-base`, `duck-wing`, `duck-glasses` PNGs |

## Notes / limits

- macOS only (primary display) for now. The window level, dock coverage, and
  `visibleOnAllWorkspaces` are tuned for macOS.
- Not wired into the Python package or its test suite — this is a standalone
  companion app under `desktop-pet/`.
