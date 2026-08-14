/**
 * The dialog contract.
 *
 * jsdom does implement `<dialog>`, so `showModal`, `close` and the `cancel`
 * event are all real here — the fallback branch in Modal.tsx is for older
 * environments and is not what these exercise.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';

import { Modal } from './Modal';
import sharedCss from './shared.module.css?raw';

const dialog = (): HTMLDialogElement => document.querySelector('dialog') as HTMLDialogElement;

describe('Modal', () => {
  it('restores the auto margin the global reset takes away', () => {
    // Centring a top-layer <dialog> is `margin: auto` against the UA's
    // `inset: 0` and nothing else. palette.css opens with
    // `* { box-sizing: border-box; margin: 0; padding: 0 }`, which zeroes it —
    // so every modal on every surface rendered pinned to the top-left corner
    // of the viewport. jsdom does not lay out or centre anything, so no
    // behavioural test can see this; the declaration is the assertion.
    //
    // The `dialog` qualifier is asserted too, and is not cosmetic: without it
    // the rule ties on specificity with every `.container > * + *` stacking
    // idiom in the codebase, and loses or wins on bundle order — which is how
    // the same modal came out centred on one board and flush to the top edge
    // on the other.
    const block = /dialog\.modal\s*\{([^}]*)\}/.exec(sharedCss)?.[1] ?? '';
    expect(block, 'no `dialog.modal` rule in shared.module.css').toBeTruthy();
    expect(block).toMatch(/margin\s*:\s*auto/);
  });

  it('is closed until asked to open', () => {
    render(
      <Modal open={false} onClose={vi.fn()} title="Choose a name">
        body
      </Modal>
    );
    expect(dialog().open).toBe(false);
  });

  it('opens modally, which is what brings the focus trap and Esc with it', () => {
    render(
      <Modal open onClose={vi.fn()} title="Choose a name">
        body
      </Modal>
    );
    expect(dialog().open).toBe(true);
    // The role and the accessible name are the two things the old div-based
    // "modals" had neither of.
    expect(screen.getByRole('dialog', { name: 'Choose a name' })).toBeTruthy();
  });

  it('reports Escape rather than closing itself behind React state', () => {
    // The native default would close the element while `open` stayed true, and
    // the next render would try to re-open a dialog the user just dismissed.
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Choose a name">
        body
      </Modal>
    );
    fireEvent(dialog(), new Event('cancel', { cancelable: true }));

    expect(onClose).toHaveBeenCalled();
    expect(dialog().open).toBe(true); // still open — state has not changed yet
  });

  it('closes when the backdrop is clicked but not the content', () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Choose a name">
        <p>body</p>
      </Modal>
    );

    fireEvent.click(screen.getByText('body'));
    expect(onClose).not.toHaveBeenCalled();

    // A backdrop click lands on the <dialog> itself — the backdrop is its
    // pseudo-element, so there is no separate node to hit.
    fireEvent.click(dialog());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps the title available to assistive tech when hidden visually', () => {
    render(
      <Modal open onClose={vi.fn()} title="Invite" hideTitle>
        body
      </Modal>
    );
    // srOnly, not display:none — the name must survive for the dialog role.
    expect(screen.getByRole('dialog', { name: 'Invite' })).toBeTruthy();
  });
});
