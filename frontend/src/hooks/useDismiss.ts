/**
 * Escape, or a pointer press outside a region, closes it.
 *
 * The same eight lines had been written twice — once in `PopoverGroup` for the
 * toolbar, once in `ReactionBar` for the emoji picker — and a card now needs a
 * third for its delete confirmation. Anything transient and dismissable wants
 * exactly this pair of dismissals, and getting either one subtly wrong (click
 * instead of pointerdown, bubble instead of capture) produces a control that
 * mostly works.
 *
 * `pointerdown`, not `click`: a click fires after mouseup, so a card drag that
 * starts under an open panel would otherwise leave it open for the whole drag.
 * Capture phase, so a handler that stops propagation cannot trap it.
 */

import { useEffect, type RefObject } from 'react';

export function useDismiss(
  open: boolean,
  ref: RefObject<HTMLElement | null>,
  onDismiss: (reason: 'escape' | 'outside') => void,
  /** A second region that also counts as inside — a panel opened in a portal. */
  alsoInside?: RefObject<HTMLElement | null>
): void {
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: Event): void => {
      const region = ref.current;
      if (!region || !(event.target instanceof Node)) return;
      if (region.contains(event.target)) return;
      if (alsoInside?.current?.contains(event.target)) return;
      onDismiss('outside');
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onDismiss('escape');
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, ref, onDismiss, alsoInside]);
}
