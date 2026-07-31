/**
 * A small mono label above a thing, set uppercase and widely tracked.
 *
 * The structural device yeaboi.ai leans on (`site.css .eyebrow`), and the way
 * the mono-forward type system announces "this is furniture, not content".
 *
 * One rule about what goes in it: **an eyebrow must encode something true about
 * what it labels.** The tempting default is a sequence number — `01 / ANALYSIS`,
 * `02 / PLANNING` — which is right on the marketing page, where the modes really
 * are presented in order. It would be wrong on the retro board: the four
 * columns are not a sequence, and numbering them would assert an order that
 * does not exist. There the eyebrow carries the count, which is information a
 * facilitator actually uses.
 *
 * So: a name, a count, a date, a source. Not decoration, and not a number for
 * the look of a number.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface EyebrowProps {
  children: ReactNode;
  /**
   * A trailing value, set apart by a middot and rendered tabular.
   *
   * Separate from `children` rather than interpolated by the caller so counts
   * line up between adjacent eyebrows — four column heads whose numbers jitter
   * as cards are added is exactly the kind of small wrongness that makes a
   * board feel unfinished.
   */
  value?: ReactNode;
  /** Tints the label with the current accent. Use for the active one only. */
  accent?: boolean;
  className?: string | undefined;
}

export function Eyebrow({ children, value, accent, className }: EyebrowProps) {
  return (
    <span className={cx(styles['eyebrow'], accent && styles['eyebrowAccent'], className)}>
      {children}
      {value !== undefined && value !== null ? (
        <>
          <span className={styles['eyebrowDot']} aria-hidden="true">
            ·
          </span>
          <span className={styles['eyebrowValue']}>{value}</span>
        </>
      ) : null}
    </span>
  );
}
