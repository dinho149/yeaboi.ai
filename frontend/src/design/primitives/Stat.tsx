/**
 * Metric tiles — a big number over a small caption.
 *
 * Port of `html_theme.stat_tile` / `.stat-grid`. The value renders in the mono
 * "numbers voice" with tabular figures, so a grid of tiles lines up and a value
 * that updates live does not make its tile twitch.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import { toneVar, type Tone } from '../tone';
import styles from './primitives.module.css';

export interface StatTileProps {
  value: ReactNode;
  label: string;
  /** Optional second line — a comparison, a denominator, a caveat. */
  hint?: string;
  /** Colour of the value. Defaults to the theme accent. */
  tone?: Tone;
  /**
   * Rendered under the label — a progress bar, a sparkline, a breakdown.
   *
   * Inside the tile rather than beside it, because a bar that describes *this*
   * number has to sit with it: standup's sprint-progress bar rendered under the
   * whole grid read as a page-level progress indicator instead of "day 7 of 10".
   */
  children?: ReactNode;
  className?: string | undefined;
}

export function StatTile({ value, label, hint, tone, children, className }: StatTileProps) {
  return (
    <div className={cx(styles['stat'], className)}>
      <div className={styles['statValue']} style={tone ? { color: toneVar(tone) } : undefined}>
        {value}
      </div>
      <div className={styles['statLabel']}>{label}</div>
      {hint ? <div className={styles['statHint']}>{hint}</div> : null}
      {children}
    </div>
  );
}

/** Auto-fitting grid of tiles. Collapses to one column on a narrow screen. */
export function StatGrid({
  children,
  className,
}: {
  children: ReactNode;
  className?: string | undefined;
}) {
  return <div className={cx(styles['statGrid'], className)}>{children}</div>;
}
