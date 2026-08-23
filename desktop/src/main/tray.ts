// The menu-bar duck. One tray for the whole app — the pet's own tray from the
// prototype folds in here, because two duck icons in one menu bar is a bug.
//
// It is also what makes the pet possible: the window can be closed while the
// duck stays on screen, so something has to be able to bring the app back and
// to quit it. That is this menu.

import { Menu, Tray, app, nativeImage } from 'electron';
import trayIconPath from '../../resources/duck-tray.png?asset';
import type { Pet } from './pet';

/** Menu-bar icons are measured in points; 20 is the conventional height. */
const TRAY_ICON_SIZE = 20;

export interface TrayActions {
  open: () => void;
  togglePet: (enabled: boolean) => void;
  quit: () => void;
}

export class AppTray {
  private tray: Tray | null = null;
  private petEnabled = false;

  constructor(
    private readonly pet: Pet,
    private readonly actions: TrayActions,
  ) {}

  create(petEnabled: boolean): void {
    this.petEnabled = petEnabled;
    const icon = nativeImage
      .createFromPath(trayIconPath)
      .resize({ width: TRAY_ICON_SIZE, height: TRAY_ICON_SIZE });
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
        { label: 'Quit yeaboi', click: () => this.actions.quit() },
      ]),
    );
  }
}
