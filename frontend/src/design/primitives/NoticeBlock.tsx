/**
 * The ⚠ caveats panel — the "here is what this report does not know" block.
 *
 * Port of `html_theme.notice_block`. Renders nothing for an empty list, which
 * is load-bearing: an empty warning box reads as "we checked and found nothing
 * to warn you about", which is a stronger claim than any caller makes.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import { Icon } from './Icon';
import styles from './primitives.module.css';

export interface NoticeBlockProps {
  title: string;
  items: readonly ReactNode[];
  /**
   * Announce the notice when it appears rather than waiting to be read.
   * Used for the board lock banner — a frozen board that says nothing is
   * indistinguishable from a broken one.
   */
  live?: boolean;
  className?: string | undefined;
}

export function NoticeBlock({ title, items, live, className }: NoticeBlockProps) {
  if (!items.length) return null;
  return (
    <div className={cx(styles['notice'], className)} {...(live ? { role: 'alert' } : {})}>
      <div className={styles['noticeTitle']}>
        <Icon name="triangle-alert" />
        {title}
      </div>
      <ul>
        {items.map((item, index) => (
          // Notice text is the item's identity — these lists are rebuilt whole
          // and carry no state, so a content-derived key is both stable and free.
          <li key={typeof item === 'string' ? item : index}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
