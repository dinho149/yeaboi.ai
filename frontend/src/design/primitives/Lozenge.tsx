/**
 * A Jira-style status lozenge — the ALL-CAPS rounded tag a tracker user reads
 * as "board status" before reading the word inside it.
 *
 * Jira has exactly three status categories (grey to-do, blue in-progress,
 * green done); `blocked` is the fourth colour this page needs because a
 * flagged impediment is the one thing a standup reader must act on. The
 * closed {@link LozengeCategory} union is the CSS-injection whitelist — the
 * same trick as `tone.ts`: payload strings never reach a class name, they are
 * mapped through {@link statusCategory}-style matchers upstream and arrive
 * here as one of four known values.
 *
 * The status word always renders inside the lozenge (uppercased by CSS, so
 * the DOM keeps the tracker's own vocabulary — "In Progress", "Doing") which
 * satisfies the house rule: never a colour alone.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export const LOZENGE_CATEGORIES = ['todo', 'inprogress', 'done', 'blocked'] as const;
export type LozengeCategory = (typeof LOZENGE_CATEGORIES)[number];

export interface LozengeProps {
  category: LozengeCategory;
  children: ReactNode;
  /** Inline size for evidence rows; omit for the card-header lozenge. */
  small?: boolean;
  title?: string;
  className?: string | undefined;
}

/** Category → module class. A Record so a new category fails the build here. */
const CATEGORY_CLASS: Record<LozengeCategory, string> = {
  todo: styles['lozengeTodo'] ?? '',
  inprogress: styles['lozengeProgress'] ?? '',
  done: styles['lozengeDone'] ?? '',
  blocked: styles['lozengeBlocked'] ?? '',
};

export function Lozenge({ category, children, small, title, className }: LozengeProps) {
  return (
    <span
      className={cx(styles['lozenge'], CATEGORY_CLASS[category], small && styles['lozengeSmall'], className)}
      title={title}
    >
      {children}
    </span>
  );
}
