// Minimal, locked-down bridge between the duck renderer and the main process.
// The renderer never touches Node directly (contextIsolation on); it only gets
// these two channels.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("pet", {
  // Tell main whether the cursor is currently over the duck, so it can toggle
  // the window between click-through and grab-able.
  setInteractive: (over) => ipcRenderer.send("pet:interactive", !!over),
  // Window-local cursor position, polled ~30fps by main.
  onCursor: (fn) => ipcRenderer.on("pet:cursor", (_e, p) => fn(p)),
  // Tray → "Come here": recenter the duck on screen.
  onRecenter: (fn) => ipcRenderer.on("pet:recenter", () => fn()),
});
