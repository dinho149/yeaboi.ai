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
 * ## The jsdom caveat
 *
 * `showModal` is a relatively recent jsdom addition. The fallback below opens
 * the dialog non-modally rather than throwing, so component tests can assert on
 * content even where the modal machinery is absent. It is a *test* fallback:
 * every browser yeaboi supports has the real thing.
 */

import { useEffect, useId, useRef, type ReactNode } from 'react';

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

export function Modal({ open, onClose, title, hideTitle, children, actions, className }: ModalProps) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open) {
      if (dialog.open) return;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', ''); // jsdom / very old browsers
    } else if (dialog.open) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    }
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
      className={cx(styles['modal'], className)}
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
