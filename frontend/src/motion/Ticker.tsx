/**
 * A number that flicks over when it changes.
 *
 * The quietest of the four liveness moments, and the only one carrying a value
 * rather than an event: a column head whose figure ticks tells a facilitator
 * *which* column moved without them having to have been watching it.
 *
 * Renders as tabular mono so the digits do not jitter sideways as they change —
 * that jitter is what makes an unstyled live counter feel cheap.
 */

import { useEffect, useRef, useState } from 'react';

import { cx } from '../runtime/cx';
import styles from './motion.module.css';

export interface TickerProps {
  value: number;
  /**
   * Accessible label, e.g. "cards in What went well".
   *
   * Without one the number is announced bare on every change, which on a busy
   * board is a stream of context-free digits. With one it is still `aria-live`
   * off by default — the caller opts in — because four columns all announcing
   * would be worse than silence.
   */
  label?: string;
  className?: string | undefined;
}

export function Ticker({ value, label, className }: TickerProps) {
  const previous = useRef(value);
  const [ticking, setTicking] = useState(false);

  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    setTicking(true);
    // Re-key the animation rather than relying on class removal: two changes
    // inside the animation window must restart it, not be swallowed.
    const timer = setTimeout(() => setTicking(false), 220);
    return () => clearTimeout(timer);
  }, [value]);

  return (
    <span
      className={cx(styles['ticker'], ticking && styles['tick'], className)}
      // Keying on the value makes preact replace the node, which is what
      // guarantees the animation restarts for every change.
      key={ticking ? value : 'idle'}
      aria-label={label}
    >
      {value}
    </span>
  );
}
