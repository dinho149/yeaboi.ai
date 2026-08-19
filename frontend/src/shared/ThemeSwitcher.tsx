/**
 * The palette picker: five swatches, one click each.
 *
 * Two changes from the version on the boards.
 *
 * **The probe hack is gone.** `buildSwatches` used to append a hidden
 * `[data-theme]` div to the body for every swatch, read `getComputedStyle` off
 * it, and remove it — a forced synchronous layout per swatch, every time the
 * picker opened, to recover values that were static all along. They now come
 * from `THEME_PREVIEW`, which a test keeps in step with palette.css.
 *
 * **The swatches are a radio group.** They were `<button>`s with a `.sel` class,
 * which tells assistive tech nothing about which one is active or that they are
 * alternatives. `role="radio"` + `aria-checked` says both, and arrow keys move
 * between them the way a radio group is expected to behave.
 */

import type { ReactNode } from 'react';

import { cx } from '../runtime/cx';
import { THEME_PREVIEW, THEMES, type Theme } from '../runtime/theme';
import styles from './shared.module.css';

export interface ThemeSwitcherProps {
  value: Theme;
  onChange(theme: Theme): void;
  /** Rendered under the swatches — the host's "cast to everyone" button. */
  footer?: ReactNode;
  className?: string | undefined;
}

export function ThemeSwitcher({ value, onChange, footer, className }: ThemeSwitcherProps) {
  const move = (delta: number): void => {
    const next = THEMES[(THEMES.indexOf(value) + delta + THEMES.length) % THEMES.length];
    if (next) onChange(next);
  };

  return (
    <div className={cx(styles['panelForm'], className)}>
      <span className={styles['fieldLabel']}>Theme</span>
      <div className={styles['swatches']} role="radiogroup" aria-label="Colour theme">
        {THEMES.map((theme) => {
          const preview = THEME_PREVIEW[theme];
          const selected = theme === value;
          return (
            <button
              key={theme}
              type="button"
              role="radio"
              aria-checked={selected}
              // Roving tabindex: one stop for the whole group, then arrow keys
              // inside it. Five separate tab stops for five alternatives is the
              // classic way to make a keyboard user's life miserable.
              tabIndex={selected ? 0 : -1}
              className={cx(styles['swatch'], selected && styles['swatchOn'])}
              // Two tones, because that is what a theme is here: the ground it
              // paints and the accent it paints on top of it.
              style={{ '--swatch-bg': preview.bg, '--swatch-accent': preview.accent } as never}
              title={theme}
              aria-label={theme}
              onClick={() => onChange(theme)}
              onKeyDown={(event) => {
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                  event.preventDefault();
                  move(1);
                } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                  event.preventDefault();
                  move(-1);
                }
              }}
            >
              <span className={styles['swatchName']}>{theme}</span>
            </button>
          );
        })}
      </div>
      {footer}
    </div>
  );
}
