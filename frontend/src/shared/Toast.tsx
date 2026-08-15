/**
 * A brief, self-dismissing message for something that happened unprompted.
 *
 * There is exactly one caller today: opening the invite panel puts the invite on
 * the clipboard without being asked, and an action nobody requested has to say so
 * or it did not happen. A button that confirms under your finger needs no toast
 * — see `CopyField` — so this is deliberately not the general feedback channel.
 *
 * `role="status"` rather than `role="alert"`: polite, so it waits for a pause
 * instead of interrupting whatever a screen reader is mid-sentence on. Nothing
 * announced here is urgent, and the same information is on screen in the panel
 * behind it.
 *
 * It renders nothing when there is no message, so a caller can hold `null` in
 * state and pass it straight through — but not immediately: the message it was
 * showing is held for one beat after the caller clears it, so the toast can
 * fold away instead of blinking out and jolting the panel it sits in.
 */

import { useEffect, useState } from 'react';

import { cx } from '../runtime/cx';
import styles from './shared.module.css';

/** How long a toast stays up. Long enough to read twice at a glance. */
export const TOAST_MS = 2600;

/** How long it takes to fold away. Matches `toastOut` in shared.module.css. */
const EXIT_MS = 220;

export interface ToastProps {
  /** The message, or `null`/`''` to render nothing. */
  message: string | null;
  /** Called when the toast has timed out, so the caller can clear its state. */
  onDismiss: () => void;
  className?: string | undefined;
}

export function Toast({ message, onDismiss, className }: ToastProps) {
  const [shown, setShown] = useState<string | null>(message);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onDismiss, TOAST_MS);
    // Re-keyed on the message, so a second toast restarts the clock rather than
    // inheriting the remains of the first one's.
    return () => clearTimeout(timer);
  }, [message, onDismiss]);

  useEffect(() => {
    if (message) {
      setShown(message);
      setLeaving(false);
      return;
    }
    if (!shown) return;
    setLeaving(true);
    const timer = setTimeout(() => {
      setShown(null);
      setLeaving(false);
    }, EXIT_MS);
    return () => clearTimeout(timer);
  }, [message, shown]);

  if (!shown) return null;

  return (
    <div
      className={cx(styles['toast'], leaving && styles['toastOut'], className)}
      role="status"
      aria-live="polite"
      // Gone to assistive tech the moment the caller says so — the fold is
      // only for the eye.
      aria-hidden={leaving ? 'true' : undefined}
    >
      {shown}
    </div>
  );
}
