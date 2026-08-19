/**
 * Text that lands a word at a time.
 *
 * For anything a model produced: it arrives as one finished string, and reading
 * it appear is the difference between a page that answered you and a page that
 * printed at you.
 *
 * The whole of it is in the DOM from the first frame — the part not yet written
 * is transparent, not absent — so nothing reflows as it fills in and whatever
 * sits above it does not move. That is also what a screen reader gets: all of
 * it, at once, rather than a word at a time.
 */

import { useEffect, useState, type ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './board.module.css';

/** How long one word waits for the next. A read-along pace, not a wait. */
export const WORD_MS = 22;

export interface WrittenProps {
  text: string;
  /** Rendered once the last word lands — a conclusion, a suggestion, a score. */
  children?: ReactNode;
  className?: string | undefined;
}

export function Written({ text, children, className }: WrittenProps) {
  const [upto, setUpto] = useState(text.length);

  useEffect(() => {
    if (!text || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setUpto(text.length);
      return undefined;
    }
    setUpto(0);
    let at = 0;
    let timer = 0;
    const write = (): void => {
      const space = text.indexOf(' ', at + 1);
      at = space === -1 ? text.length : space;
      setUpto(at);
      if (at < text.length) timer = window.setTimeout(write, WORD_MS);
    };
    timer = window.setTimeout(write, WORD_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const done = upto >= text.length;

  return (
    <>
      <p className={className}>
        {text.slice(0, upto)}
        <span className={styles['unwritten']}>{text.slice(upto)}</span>
      </p>
      {/* Holds its place from the start and fades in once the reasoning above it
          is finished — reading the verdict first gives the argument away. */}
      {children ? <div className={cx(!done && styles['unwritten'])}>{children}</div> : null}
    </>
  );
}
