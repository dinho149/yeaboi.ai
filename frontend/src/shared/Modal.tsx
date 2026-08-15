/**
 * A real modal dialog, on the native `<dialog>` element.
 *
 * ## What this fixes
 *
 * Every "modal" on both boards today — `#code-modal`, `#modal`, `#invite-modal`,
 * `#edit-modal` — is a plain `<div>` that gets a `hidden` class toggled. That
 * means: no `role="dialog"`, so assistive tech never announces one opened; no
 * focus trap, so Tab walks straight out into the board behind it; no focus
 * restore, so closing it dumps you at the top of the document; and Esc closes
 * only the popovers, never these.
 *
 * `showModal()` gives all four for free — the dialog role, the trap, Esc, and an
 * inert background — plus `::backdrop`, which replaces a hand-rolled overlay
 * div. This is one of the few places where the platform primitive is strictly
 * better than anything worth writing.
 *
 * ## Closing takes a beat
 *
 * A `<dialog>` leaves the box tree the moment it closes, so an exit keyframe
 * never gets a frame to run in — which is why this used to vanish. The element
 * is now held open for the length of the exit animation and closed after it, so
 * the dialog and its backdrop can fade together.
 *
 * ## Closing takes a beat
 *
 * A `<dialog>` leaves the box tree the moment it closes, so an exit keyframe
 * never gets a frame to run in — which is why this used to vanish. The element
 * is held open for the length of the exit animation and closed after it, so the
 * dialog and its backdrop can fade together.
 *
 * ## The jsdom caveat
 *
 * `showModal` is a relatively recent jsdom addition. The fallback below opens
 * the dialog non-modally rather than throwing, so component tests can assert on
 * content even where the modal machinery is absent. It is a *test* fallback:
 * every browser yeaboi supports has the real thing.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { cx } from '../runtime/cx';
import styles from './shared.module.css';

export interface ModalProps {
  open: boolean;
  onClose(): void;
  /** Accessible name. Rendered as the visible heading unless `hideTitle`. */
  title: string;
  hideTitle?: boolean;
  children: ReactNode;
  /** Rendered in the footer row — confirm/cancel and friends. */
  actions?: ReactNode;
  className?: string | undefined;
}

/** How long the dialog is held open to fade. Matches `modalOut` in the CSS. */
const EXIT_MS = 180;

export function Modal({ open, onClose, title, hideTitle, children, actions, className }: ModalProps) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const [closing, setClosing] = useState(false);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const shut = (): void => {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    };
    if (open) {
      setClosing(false);
      if (dialog.open) return;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', ''); // jsdom / very old browsers
      return;
    }
    if (!dialog.open) return;
    setClosing(true);
    const timer = setTimeout(() => {
      setClosing(false);
      shut();
    }, EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // `cancel` is Esc. Prevent the default close so React state stays the single
    // source of truth — otherwise the element closes itself, `open` stays true,
    // and the next render tries to re-open a dialog the user just dismissed.
    const onCancel = (event: Event): void => {
      event.preventDefault();
      onClose();
    };
    // `close` also fires for form method="dialog" submits and browser chrome.
    const onNativeClose = (): void => onClose();
    dialog.addEventListener('cancel', onCancel);
    dialog.addEventListener('close', onNativeClose);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
      dialog.removeEventListener('close', onNativeClose);
    };
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      className={cx(styles['modal'], closing && styles['modalOut'], className)}
      aria-labelledby={titleId}
      // Clicking the backdrop should dismiss. The click lands on the <dialog>
      // itself (the backdrop is its pseudo-element), so comparing target to
      // currentTarget distinguishes it from a click on the content inside.
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={styles['modalBody']}>
        <h2 id={titleId} className={cx(styles['modalTitle'], hideTitle && styles['srOnly'])}>
          {title}
        </h2>
        {children}
        {actions ? <div className={styles['modalActions']}>{actions}</div> : null}
      </div>
    </dialog>
  );
}
