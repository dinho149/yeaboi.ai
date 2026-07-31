/**
 * The frame every exported report sits in.
 *
 * An export is the one surface where the terminal chrome was never in question:
 * the file really was produced by a tool someone ran in a terminal, and the
 * header is that tool's mark. That turned out to be true of the live boards and
 * the share gate too — they are served *out of* that same terminal over a
 * tunnel — so the masthead itself now lives in `shared/PageShell`, and this is
 * the adapter that owns the one thing only an export has: a theme picker that
 * is part of the page rather than tucked inside a board's popover.
 *
 * The switcher is the five-swatch picker rather than a cycle button. Cycling
 * made sense when the control was one `onclick` string in a page with no
 * runtime; with a real component there is no reason to make someone click four
 * times to get from midnight to forest. It prints as nothing, along with the
 * contents nav — both are controls, and a control on paper is furniture that
 * cost a reader ink.
 */

import { useState, type ReactNode } from 'react';

import { setTheme, type Theme } from '../runtime/theme';
import { PageShell, ThemeSwitcher } from '../shared';
import type { ExportChrome } from './boot';

export interface ShellProps {
  chrome: ExportChrome;
  /** The palette already applied to the document, before React mounted. */
  theme: Theme;
  children: ReactNode;
}

export function Shell({ chrome, theme: initial, children }: ShellProps) {
  const [theme, setThemeState] = useState<Theme>(initial);

  const choose = (next: Theme): void => {
    setThemeState(next);
    setTheme(next);
  };

  return (
    <PageShell chrome={chrome} themeSwitcher={<ThemeSwitcher value={theme} onChange={choose} />}>
      {children}
    </PageShell>
  );
}
