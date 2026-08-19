/**
 * One sentence, over the whole board, for a couple of seconds.
 *
 * For the thing a surface has to tell one person right now — your turn, you are
 * up, it is your go. A line inside the region that raised it is the wrong shape
 * for that: the region may be one tab of three, so the person it is addressed to
 * can be reading something else and never see it, and once seen there is nothing
 * more to do with it, yet it stays.
 *
 * A portal, because it belongs to the screen rather than to the component it is
 * declared in — and that component is very often inside something hidden.
 */

import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import styles from './board.module.css';

/** How long it holds the screen, in ms. */
export const ANNOUNCE_MS = 2400;

export interface AnnounceProps {
  /** Raise it. Going true is the trigger; staying true does not re-raise. */
  when: boolean;
  children: ReactNode;
  /** Overrides how long it stays. */
  ms?: number;
}

export function Announce({ when, children, ms = ANNOUNCE_MS }: AnnounceProps) {
  const [showing, setShowing] = useState(false);

  useEffect(() => {
    if (!when) return undefined;
    setShowing(true);
    const timer = window.setTimeout(() => setShowing(false), ms);
    return () => window.clearTimeout(timer);
  }, [when, ms]);

  if (!showing) return null;
  return createPortal(
    // `role="status"` rather than `alert`: this is news, not an error, and alert
    // interrupts whatever a screen reader is mid-sentence on.
    <div className={styles['veil']} role="status">
      <p className={styles['announce']}>{children}</p>
    </div>,
    document.body
  );
}
