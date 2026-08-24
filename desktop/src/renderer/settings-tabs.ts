// The tab → section arrangement for the Settings page, mirroring the TUI's
// _SETTINGS_TAB_SECTIONS.
//
// routes.json is the source, for the same reason it is the source of the
// routes: it is what the manifest carries into Python, where
// tests/unit/test_tui_parity.py holds the section list against the terminal's
// — a settings section that lands in one surface only fails the build.

import registry from './routes.json';

export interface SettingsTab {
  route: string;
  title: string;
  sections: readonly string[];
}

export const SETTINGS_TABS: readonly SettingsTab[] = registry.settings_tabs;
