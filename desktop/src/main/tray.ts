// The menu-bar duck. One tray for the whole app — the pet's own tray from the
// prototype folds in here, because two duck icons in one menu bar is a bug.
//
// It is also what makes the pet possible: the window can be closed while the
// duck stays on screen, so something has to be able to bring the app back and
// to quit it. That is this menu.

import { Menu, Tray, app, nativeImage } from 'electron';
import trayIconPath from '../../resources/duck-tray.png?asset';
// The macOS menu bar paints template images itself, so the duck goes in as
// alpha only and comes out right in light mode, dark mode and under a tint.
// Its @2x sibling ships beside it in resources/ and Electron picks it up by
// name — an import would only re-emit the same file under a second one.
import trayTemplatePath from '../../resources/duck-trayTemplate.png?asset';
import type { Pet } from './pet';
import type { UpdateState } from './updater';

/** Menu-bar icons are measured in points; 20 is the conventional height. */
const TRAY_ICON_SIZE = 20;

/** The update item says where the update got to, not what the menu does — a
 *  "Check for updates…" that already found one reads as if nothing happened. */
export function updateLabel(state: UpdateState): string {
  switch (state.kind) {
    case 'checking':
      return 'Checking for updates…';
    case 'available':
      return `Download yeaboi ${state.version}`;
    case 'downloading':
      return `Downloading ${state.version} — ${state.percent}%`;
    case 'ready':
      return `Restart to update to ${state.version}`;
    case 'error':
      return 'Check for updates… (last check failed)';
    case 'unsupported':
      return 'Updates are managed outside the app';
    default:
      return 'Check for updates…';
  }
}

export interface TrayActions {
  open: () => void;
  about: () => void;
  update: () => void;
  togglePet: (enabled: boolean) => void;
  quit: () => void;
}

export class AppTray {
  private tray: Tray | null = null;
  private petEnabled = false;
  private update: UpdateState = { kind: 'idle' };

  constructor(
    private readonly pet: Pet,
    private readonly actions: TrayActions,
  ) {}

  create(petEnabled: boolean): void {
    this.petEnabled = petEnabled;
    const template = process.platform === 'darwin';
    const icon = nativeImage
      .createFromPath(template ? trayTemplatePath : trayIconPath)
      .resize({ width: TRAY_ICON_SIZE, height: TRAY_ICON_SIZE });
    icon.setTemplateImage(template);
    this.tray = new Tray(icon);
    this.tray.setToolTip('yeaboi');
    this.tray.on('click', () => this.actions.open());
    this.render();
  }

  /** Re-read the pet state onto the menu (a checkbox that lies is worse than
   *  no checkbox — the toggle can also be flipped from the app's own settings). */
  setPetEnabled(enabled: boolean): void {
    this.petEnabled = enabled;
    this.render();
  }

  setUpdateState(state: UpdateState): void {
    this.update = state;
    this.render();
  }

  destroy(): void {
    this.tray?.destroy();
    this.tray = null;
  }

  private render(): void {
    if (!this.tray) return;
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'Open yeaboi', click: () => this.actions.open() },
        { type: 'separator' },
        {
          label: 'Duck on the desktop',
          type: 'checkbox',
          checked: this.petEnabled,
          click: (item) => this.actions.togglePet(item.checked),
        },
        // The nudges only mean something while there is a duck to nudge.
        { label: 'Sit higher', enabled: this.petEnabled, click: () => this.pet.nudge(6) },
        { label: 'Sit lower', enabled: this.petEnabled, click: () => this.pet.nudge(-6) },
        { label: 'Come here', enabled: this.petEnabled, click: () => this.pet.recenter() },
        { type: 'separator' },
        { label: `yeaboi ${app.getVersion()}`, enabled: false },
        { label: updateLabel(this.update), enabled: this.update.kind !== 'unsupported', click: () => this.actions.update() },
        { label: 'About yeaboi', click: () => this.actions.about() },
        { label: 'Quit yeaboi', click: () => this.actions.quit() },
      ]),
    );
  }
}
