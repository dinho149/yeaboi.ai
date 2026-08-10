/**
 * A modal dialog, on top of the native `<dialog>` element.
 *
 * Native rather than a div-with-a-backdrop, because `showModal()` gives four
 * things for free that hand-rolled dialogs almost always get wrong: the focus
 * trap, the inert background, Escape-to-close, and the top-layer stacking that
 * makes z-index fights impossible. What it does *not* give is close-on-backdrop
 * click, which is the one behaviour added below.
 *
 * Focus is returned to the element that opened the dialog on close — the
 * browser does this for `showModal()`, and it is the difference between closing
 * a dialog and losing your place on the page.
 */

import type { ComponentChildren } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** The accessible name. Rendered as the heading. */
  title: string;
  children: ComponentChildren;
  /** Actions, rendered in the footer. */
  footer?: ComponentChildren;
  className?: string | undefined;
}

export function Modal({ open, onClose, title, children, footer, className }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // `open` as an attribute renders a *non-modal* dialog with no focus trap
    // and no top layer, so it has to be driven by the methods instead.
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // Escape fires `cancel`, which would close the element without telling the
    // owner — leaving `open` true and the dialog shut until it is toggled twice.
    const onCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => dialog.removeEventListener('cancel', onCancel);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      className={cx(styles['modal'], className)}
      aria-labelledby="modal-title"
      onClick={(event) => {
        // The dialog element's box *is* the backdrop, so a click landing on the
        // element itself rather than a child means the backdrop was hit.
        if (event.target === ref.current) onClose();
      }}
    >
      <div className={styles['modalBody']}>
        <h2 className={styles['modalTitle']} id="modal-title">
          {title}
        </h2>
        {children}
        {footer ? <div className={styles['modalFooter']}>{footer}</div> : null}
      </div>
    </dialog>
  );
}
