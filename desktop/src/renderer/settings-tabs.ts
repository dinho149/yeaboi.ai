// The tab → section arrangement for the Settings page, mirroring the TUI's
// _SETTINGS_TAB_SECTIONS. A plain module (no JSX, no design-system imports)
// so the vitest suite can hold it against routes.json.

export interface SettingsTab {
  route: string;
  title: string;
  sections: readonly string[];
}

export const SETTINGS_TABS: readonly SettingsTab[] = [
  {
    route: '/settings/credentials',
    title: 'Credentials',
    sections: ['provider', 'jira', 'azure', 'github', 'notion', 'slack'],
  },
  { route: '/settings/sharing', title: 'Sharing', sections: ['sharing'] },
  { route: '/settings/system', title: 'System', sections: ['storage', 'standup', 'voice', 'advanced'] },
];
