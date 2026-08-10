/**
 * A loading placeholder.
 *
 * `aria-hidden`, always. A skeleton is a *visual* promise that content is
 * coming; announcing "loading loading loading" once per shimmering rectangle is
 * noise. The surrounding region carries the real `aria-busy`, which is what a
 * screen reader should hear.
 *
 * Sized in `ch` and `em` rather than pixels so a placeholder is proportional to
 * the text it stands in for, which also keeps it out of the raw-spacing guard.
 */

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface SkeletonProps {
  /** Width in characters, matched to the text this stands in for. */
  chars?: number;
  /** Height in line-heights. */
  lines?: number;
  className?: string | undefined;
}

export function Skeleton({ chars = 12, lines = 1, className }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={cx(styles['skeleton'], className)}
      style={{ width: `${chars}ch`, height: `${lines * 1.2}em` }}
    />
  );
}

/** A stack of skeleton lines, for a paragraph or a table cell column. */
export function SkeletonLines({ count = 3 }: { count?: number }) {
  return (
    <span className={styles['skeletonStack']} aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        // Descending widths read as text rather than as bars.
        <Skeleton key={index} chars={index === count - 1 ? 18 : 32} />
      ))}
    </span>
  );
}
