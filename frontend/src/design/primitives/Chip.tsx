/**
 * A pill label — the workhorse of every yeaboi surface (priorities, disciplines,
 * story points, ticket keys, statuses).
 *
 * Port of `html_theme.chip`, with the runtime `kind` string replaced by the
 * {@link Tone} union. The `href` variant exists because ticket chips link to a
 * tracker; it routes through {@link safeUrl}, so a ticket whose URL is
 * `javascript:alert(1)` renders as plain text rather than a live link.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import { safeUrl } from '../../runtime/url';
import { toneMix, toneVar, type Tone } from '../tone';
import styles from './primitives.module.css';

export interface ChipProps {
  children: ReactNode;
  /** Semantic colour. Omit for the neutral grey chip. */
  tone?: Tone;
  /** Makes the chip a link. Unsafe schemes degrade to a plain chip. */
  href?: string;
  title?: string;
  className?: string | undefined;
}

function toneStyle(tone: Tone | undefined): Record<string, string> | undefined {
  if (!tone) return undefined;
  return {
    color: toneVar(tone),
    background: toneMix(tone, 16),
    borderColor: toneMix(tone, 40),
  };
}

export function Chip({ children, tone, href, title, className }: ChipProps) {
  const style = toneStyle(tone);
  const safe = href ? safeUrl(href) : '';

  if (safe) {
    return (
      <a
        className={cx(styles['chip'], styles['chipLink'], className)}
        style={style}
        href={safe}
        title={title}
        // A tracker link opens a different origin; without noreferrer the target
        // learns the tunnel URL, which is the access credential for the board.
        target="_blank"
        rel="noopener noreferrer"
      >
        {children}
      </a>
    );
  }

  return (
    <span className={cx(styles['chip'], className)} style={style} title={title}>
      {children}
    </span>
  );
}
