// The pet renderer's whole capability surface. It has no backend access at all:
// the duck is told things by main, and asks for exactly one — open a page.

import { contextBridge, ipcRenderer } from 'electron';

export interface PetBridge {
  /** Whether the cursor is over the duck, so main can flip click-through off. */
  setInteractive: (over: boolean) => void;
  onCursor: (fn: (point: { x: number; y: number }) => void) => void;
  onConfig: (fn: (config: unknown) => void) => void;
  onNudge: (fn: (delta: number) => void) => void;
  onRecenter: (fn: () => void) => void;
  /** Something happened while nobody was looking — say it. */
  onNotice: (fn: (notice: { quip: string; sticky: boolean; route: string }) => void) => void;
  /** A click on a duck holding a question: open the page that answers it. */
  open: (route: string) => void;
}

const bridge: PetBridge = {
  setInteractive: (over) => ipcRenderer.send('pet:interactive', !!over),
  onCursor: (fn) => {
    ipcRenderer.on('pet:cursor', (_event, point) => fn(point));
  },
  onConfig: (fn) => {
    ipcRenderer.on('pet:config', (_event, config) => fn(config));
  },
  onNudge: (fn) => {
    ipcRenderer.on('pet:nudge', (_event, delta) => fn(delta));
  },
  onRecenter: (fn) => {
    ipcRenderer.on('pet:recenter', () => fn());
  },
  onNotice: (fn) => {
    ipcRenderer.on('pet:notice', (_event, notice) => fn(notice));
  },
  open: (route) => ipcRenderer.send('pet:open', route),
};

contextBridge.exposeInMainWorld('pet', bridge);
