/**
 * A labelled value with a copy button beside it.
 *
 * The invite panel used to render the join code and the share URL as plain text,
 * which is fine right up until someone has to get one of them into a chat window
 * — at which point they are selecting an eight-character code by hand on a
 * phone, or reading a `trycloudflare.com` hostname out loud.
 *
 * The value stays visible and selectable either way. The button is the fast
 * path, not the only path: `copyText` can be refused (an insecure context, a
 * click the browser considers too old), and when it is, what is on screen is
 * still the answer.
 *
 * The confirmation is on the button itself rather than in a toast. A toast is
 * right for something that happened without being asked — the auto-copy when the
 * panel opens — but for a button you just pressed, feedback anywhere other than
 * under your finger is feedback you have to go looking for.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { copyText } from '../runtime/clipboard';
import { cx } from '../runtime/cx';
import { IconButton } from './IconButton';
import styles from './shared.module.css';

/** How long the button holds its confirmation before returning to its label. */
const CONFIRM_MS = 1600;

export interface CopyFieldProps {
  /** Short uppercase label, e.g. "Code" or "Link". */
  label: string;
  /** The text shown, and the text copied. */
  value: string;
  /**
   * Render the value in the mono voice with wide tracking.
   *
   * For the join code specifically: it is read aloud and typed by hand, and
   * 0/O and 1/I are the entire failure mode.
   */
  mono?: boolean;
  className?: string | undefined;
}

export function CopyField({ label, value, mono, className }: CopyFieldProps) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    []
  );

  const copy = useCallback(async () => {
    const ok = await copyText(value);
    setState(ok ? 'copied' : 'failed');
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), CONFIRM_MS);
  }, [value]);

  // The accessible name changes with the state, so a screen reader hears the
  // outcome from the control it just activated rather than from a live region
  // somewhere else on the page.
  const buttonLabel =
    state === 'copied' ? `${label} copied` : state === 'failed' ? `Could not copy ${label}` : `Copy ${label}`;

  return (
    <div className={cx(styles['copyField'], className)}>
      <span className={styles['fieldLabel']}>{label}</span>
      <span className={cx(styles['copyValue'], mono && styles['codeValue'])}>{value}</span>
      <IconButton
        icon={state === 'copied' ? '✓' : '⧉'}
        label={buttonLabel}
        onClick={copy}
        compact
        // Transient feedback that clears itself, not a toggle — see `emphasis`
        // in Button.tsx.
        emphasis={state === 'copied'}
        className={styles['copyBtn']}
      />
    </div>
  );
}
