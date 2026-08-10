/**
 * Transient messages.
 *
 * The region is a live region that exists *before* a message arrives — an
 * `aria-live` container inserted at the same moment as its content is not
 * announced by most screen readers, which is the single most common way toasts
 * end up silent for the people relying on them.
 *
 * `polite` rather than `assertive` for everything except a danger tone: an
 * assertive live region interrupts whatever is being read, and "Saved" is not
 * worth interrupting anyone.
 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks';

import { cx } from '../../runtime/cx';
import type { Tone } from '../tone';
import styles from './primitives.module.css';

export interface Toast {
  id: number;
  message: string;
  tone?: Tone;
}

export const TOAST_TTL_MS = 5000;

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message: string, tone?: Tone) => {
      const id = ++next.current;
      setToasts((current) => [...current, tone ? { id, message, tone } : { id, message }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), TOAST_TTL_MS),
      );
      return id;
    },
    [dismiss],
  );

  // Clearing on unmount matters here: a pending timer calling setState after
  // the owner is gone is the classic React warning, and with toasts it is
  // guaranteed rather than rare because they are fired right before navigation.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  return { toasts, push, dismiss };
}

export function ToastRegion({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  const urgent = toasts.some((toast) => toast.tone === 'danger' || toast.tone === 'critical');
  return (
    <div
      className={styles['toastRegion']}
      role={urgent ? 'alert' : 'status'}
      aria-live={urgent ? 'assertive' : 'polite'}
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={cx(styles['toast'], toast.tone && styles[`toast-${toast.tone}`])}
        >
          <span>{toast.message}</span>
          <button
            type="button"
            className={styles['toastClose']}
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss"
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  );
}
