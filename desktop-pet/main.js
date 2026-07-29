// yeaboi duck desktop pet — Electron main process.
//
// The whole trick that makes a "desktop pet" work: one transparent, frameless,
// always-on-top window stretched across the entire display (bounds, NOT
// workArea, so it also covers the dock). It stays CLICK-THROUGH by default
// (`setIgnoreMouseEvents(true, {forward:true})`) so every app underneath keeps
// working normally — but with `forward:true` the renderer still receives
// mousemove events, which is how the duck knows where your cursor is.
//
// When the cursor is actually over the duck's little hitbox, the renderer tells
// us (IPC `pet:interactive`) and we flip the window solid for that instant so
// the click/drag lands on the duck; when the cursor leaves, we go click-through
// again. That toggle is the only reason the duck is grab-able at all.

const { app, BrowserWindow, ipcMain, Tray, Menu, screen, nativeImage } = require("electron");
const path = require("path");
const { execFile } = require("child_process");

// Ask macOS where the Dock actually is on screen (its list of tiles), so the
// duck can stand ON it and walk off its edges onto the desktop floor. This uses
// the Accessibility API via System Events — the first call prompts for the
// Accessibility permission; if it's denied we simply fall back to floor-only.
function queryDockRect(cb) {
  const script = [
    'tell application "System Events" to tell process "Dock"',
    "set p to position of list 1",
    "set s to size of list 1",
    'return ((item 1 of p) as text) & "," & ((item 2 of p) as text) & "," & ((item 1 of s) as text) & "," & ((item 2 of s) as text)',
    "end tell",
  ].flatMap((l) => ["-e", l]);
  execFile("osascript", script, { timeout: 1500 }, (err, stdout) => {
    if (err) return cb(null);
    const nums = String(stdout).trim().split(",").map(Number);
    if (nums.length !== 4 || nums.some((n) => Number.isNaN(n))) return cb(null);
    cb({ x: nums[0], y: nums[1], w: nums[2], h: nums[3] }); // global screen coords (points)
  });
}

let win = null;
let tray = null;

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const { x, y, width, height } = display.bounds; // full bounds → covers the dock
  // The dock's height is the gap between the full display bottom and the
  // usable workArea bottom. The duck stands ON TOP of the dock, so the renderer
  // needs this inset to place the ground line there instead of the screen edge.
  const wa = display.workArea;
  const bottomInset = Math.max(0, y + height - (wa.y + wa.height));

  win = new BrowserWindow({
    x,
    y,
    width,
    height,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false, // never steal focus from the app you're working in
    // `screen-saver` is the highest normal level — floats the duck above the
    // dock and other always-on-top windows.
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  // Follow you across every Space / desktop, and stay visible even over a
  // full-screen app.
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  // Start fully click-through, but forward move events so the duck can track
  // the cursor across the whole screen.
  win.setIgnoreMouseEvents(true, { forward: true });

  win.loadFile(path.join(__dirname, "index.html"));

  // Surface renderer problems in the terminal (the window has no chrome/devtools
  // by default, so without this a script error would be invisible).
  win.webContents.on("console-message", (_e, level, message, line, sourceId) => {
    if (level >= 2) console.error(`[renderer] ${message} (${sourceId}:${line})`);
  });
  win.webContents.on("did-fail-load", (_e, code, desc) => console.error(`[load-fail] ${code} ${desc}`));
  win.webContents.on("render-process-gone", (_e, d) => console.error(`[render-gone] ${d.reason}`));
  // Push layout config (screen + dock geometry, in window-local coords) to the
  // renderer. Re-polled on a timer because the dock can move/resize/hide.
  const sendConfig = () => {
    if (!win || win.isDestroyed()) return;
    queryDockRect((rect) => {
      const dock = rect
        ? { x: rect.x - bounds.x, top: rect.y - bounds.y, w: rect.w, h: rect.h, present: true }
        : { present: false };
      win.webContents.send("pet:config", { bottomInset, dock });
    });
  };
  win.webContents.once("did-finish-load", () => {
    console.log(`[pet] renderer loaded ok (dock inset ${bottomInset}px)`);
    sendConfig();
  });
  const cfgTimer = setInterval(sendConfig, 2000);
  win.on("closed", () => clearInterval(cfgTimer));

  // The renderer decides, frame by frame, whether the pointer is over the duck.
  // `over === true` → make the window solid so the click/drag hits the duck.
  ipcMain.on("pet:interactive", (_evt, over) => {
    if (!win || win.isDestroyed()) return;
    win.setIgnoreMouseEvents(!over, { forward: true });
  });

  // Robust cursor feed: rather than rely on forwarded DOM mousemove events
  // (which only fire while the pointer is over the window and can be flaky when
  // click-through), we poll the OS cursor position and hand the renderer
  // window-local coords. This is what the duck uses to flee and to decide when
  // the pointer is over its hitbox.
  const bounds = display.bounds;
  const feed = setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint();
    win.webContents.send("pet:cursor", { x: p.x - bounds.x, y: p.y - bounds.y });
  }, 16);
  win.on("closed", () => clearInterval(feed));
}

function createTray() {
  // A menubar duck is the only chrome this app has — the window is frameless
  // and click-through, so quitting has to live here.
  const img = nativeImage
    .createFromPath(path.join(__dirname, "assets", "duck-glasses.png"))
    .resize({ width: 18, height: 18 });
  img.setTemplateImage(false);
  tray = new Tray(img);
  tray.setToolTip("yeaboi duck");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "yeaboi duck 🦆", enabled: false },
      { type: "separator" },
      { label: "Sit Higher ⬆", click: () => win && win.webContents.send("pet:nudge", 6) },
      { label: "Sit Lower ⬇", click: () => win && win.webContents.send("pet:nudge", -6) },
      { label: "Come here (recenter)", click: () => win && win.webContents.send("pet:recenter") },
      { type: "separator" },
      { label: "Quit", role: "quit" },
    ])
  );
}

app.whenReady().then(() => {
  // The pet is an overlay, not a real app — keep it out of the dock/app-switcher.
  if (app.dock) app.dock.hide();
  createWindow();
  createTray();
});

// A pet has no windows to "reopen"; keep it alive with no windows and quit only
// from the tray.
app.on("window-all-closed", (e) => e.preventDefault());
