/**
 * Settings: who you are, how it looks, and the one security control.
 *
 * It was a stub reading `TODO(design)`, which is fine for a placeholder and
 * bad as a resting state — two of the three things here already existed and
 * were simply unreachable.
 *
 * **Theme.** Every other browser surface has a switcher (the boards, the gate,
 * the deck) and the app did not, so the one screen a person would live in was
 * the only one stuck on the default. It reuses `runtime/theme.ts` and the same
 * storage key, so a theme chosen here is the theme an exported report opens
 * with.
 *
 * **Sign out everywhere.** `SessionStore.revoke_all` has existed since the
 * substrate commit with nothing calling it. A session that cannot be revoked
 * from a device you still have is not much of a session.
 *
 * TODO(design): a stack of labelled rows. Whether settings is a page, a drawer
 * or a modal is a product decision this does not foreclose.
 */

import { useState } from 'react';

import { Button } from '../design/primitives';
import { applyTheme, setTheme, storedTheme, THEMES, type Theme } from '../runtime/theme';
import { del } from './api';
import type { User } from './types';
import styles from './app.module.css';

export function Settings({ user, onSignedOut, notify }: {
  user: User;
  onSignedOut: () => void;
  notify: (message: string) => void;
}) {
  const [theme, setThemeState] = useState<Theme>(storedTheme() ?? 'midnight');
  const [busy, setBusy] = useState(false);

  function choose(next: Theme) {
    setThemeState(next);
    // Apply and persist together: applying without storing means the choice is
    // lost on reload, and storing without applying means it looks broken now.
    applyTheme(next);
    setTheme(next);
  }

  async function signOutEverywhere() {
    setBusy(true);
    const result = await del('/api/auth/sessions');
    setBusy(false);
    if (!result.ok) {
      notify(result.error);
      return;
    }
    onSignedOut();
  }

  return (
    <section className={styles.settings}>
      <div className={styles.settingRow}>
        <span className={styles.label}>Signed in as</span>
        <span>{user.email}</span>
      </div>

      <div className={styles.settingRow}>
        <span className={styles.label}>Theme</span>
        <div className={styles.themeRow}>
          {THEMES.map((option) => (
            <Button
              key={option}
              size="s"
              tone={option === theme ? 'primary' : 'default'}
              active={option === theme}
              onClick={() => choose(option)}
            >
              {option}
            </Button>
          ))}
        </div>
      </div>

      <div className={styles.settingRow}>
        <span className={styles.label}>Security</span>
        <Button tone="danger" disabled={busy} aria-busy={busy || undefined} onClick={signOutEverywhere}>
          Sign out on every device
        </Button>
      </div>
    </section>
  );
}
