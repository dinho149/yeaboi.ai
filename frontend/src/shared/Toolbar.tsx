/**
 * The app bar both boards wear.
 *
 * A layout shell, not a feature: brand on the left, presence next to it, then
 * whatever the mode needs, then the tool cluster on the right. Retro and poker
 * had visually similar headers built from two independent piles of CSS, which
 * is why they drifted — different heights, different gaps, different breakpoint
 * behaviour on the same phone.
 *
 * It is a `<header>` with `role="banner"` semantics by placement, and the tool
 * cluster is a real `role="toolbar"`, so a screen reader user can jump to it
 * instead of tabbing through the board to reach the timer.
 */

import type { ReactNode } from 'react';

import { Wordmark } from '../design/primitives';
import { cx } from '../runtime/cx';
import { PopoverGroup } from './Popover';
import styles from './shared.module.css';

export interface ToolbarProps {
  /**
   * The mode name, set in the block-glyph display face.
   *
   * A string goes through {@link Wordmark}; pass a node to opt out.
   *
   * Optional, and both boards now omit it: the `PageShell` masthead directly
   * above sets the same word in the six-row face, where there is room for it to
   * be the identity rather than a label. Two wordmarks sixty pixels apart is
   * duplication, not consistency. A surface that wears this bar *without* a
   * masthead should still pass one.
   */
  brand?: ReactNode;
  /** Sits left of the brand — the duck, on the boards that have one. */
  mark?: ReactNode;
  /** Small muted line beside the brand — a card count, a ticket position. */
  subtitle?: ReactNode;
  /** Presence row, focus controls — anything mode-specific and left-aligned. */
  children?: ReactNode;
  /** The right-hand cluster. Popovers inside it are mutually exclusive. */
  tools?: ReactNode;
  className?: string | undefined;
}

export function Toolbar({ brand, mark, subtitle, children, tools, className }: ToolbarProps) {
  return (
    <header className={cx(styles['appbar'], className)}>
      {mark ? <span className={styles['brandMark']}>{mark}</span> : null}
      <div className={styles['brand']}>
        {typeof brand === 'string' ? (
          <Wordmark text={brand} className={styles['brandTitle']} />
        ) : brand ? (
          <span className={styles['brandTitle']}>{brand}</span>
        ) : null}
        {subtitle ? <span className={styles['brandSub']}>{subtitle}</span> : null}
      </div>

      {/* The spacer goes before the mode's own controls when there is no tool
          cluster to push them away from — otherwise a bar whose tools have been
          docked elsewhere bunches everything against the left edge. */}
      {tools ? null : <div className={styles['spacer']} />}

      {children}

      {tools ? (
        <>
          <div className={styles['spacer']} />
          <div className={styles['tools']} role="toolbar" aria-label="Board tools">
            <PopoverGroup>{tools}</PopoverGroup>
          </div>
        </>
      ) : null}
    </header>
  );
}
