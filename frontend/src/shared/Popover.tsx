/**
 * A toolbar popover: a trigger button plus a panel, one open at a time.
 *
 * The old implementation was a module-level `POPS` map of popover id → button
 * id, a `closePops()` that looped over all of them, and a document click
 * listener that guessed from `e.target.closest('.pop')`. Adding a popover meant
 * remembering to add a row to that map. Here the group is a context: a popover
 * that renders inside `<PopoverGroup>` participates automatically.
 *
 * ## The accessibility half
 *
 * The old buttons had none of this. Each trigger now carries `aria-expanded`
 * and `aria-controls`, so a screen reader says "collapsed"/"expanded" instead
 * of reading an unexplained button; Escape closes the open panel wherever focus
 * is; and closing returns focus to the trigger, so keyboard users are not
 * dropped at the top of the document.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { cx } from '../runtime/cx';
import styles from './shared.module.css';

interface PopoverGroupValue {
  openId: string | null;
  toggle(id: string): void;
  close(): void;
}

const PopoverGroupContext = createContext<PopoverGroupValue | null>(null);

/**
 * Wraps a toolbar so its popovers are mutually exclusive.
 *
 * Also owns the two global dismissals — a pointer-down outside any panel, and
 * Escape — because both are properties of the *group*, not of any one popover.
 */
export function PopoverGroup({ children }: { children: ReactNode }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setOpenId(null), []);
  const toggle = useCallback((id: string) => setOpenId((current) => (current === id ? null : id)), []);

  useEffect(() => {
    if (openId === null) return;
    const onPointerDown = (event: PointerEvent): void => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) close();
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close();
    };
    // pointerdown, not click: a click fires after mouseup, so dragging a card
    // that starts under an open panel would otherwise leave it open the whole
    // way. Capture phase so a handler that stops propagation cannot trap it.
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [openId, close]);

  const value = useMemo(() => ({ openId, toggle, close }), [openId, toggle, close]);

  return (
    <PopoverGroupContext.Provider value={value}>
      <div ref={rootRef} className={styles['popoverGroup']}>
        {children}
      </div>
    </PopoverGroupContext.Provider>
  );
}

export interface PopoverProps {
  /** Trigger content. Wrap the glyph in `<span aria-hidden>` — see IconButton. */
  trigger: ReactNode;
  /** Accessible name for the trigger, e.g. "Music". */
  label: string;
  children: ReactNode;
  /** Anchor the panel to the left edge instead of the right. */
  align?: 'left' | 'right';
  /**
   * Which side of the trigger the panel opens on.
   *
   * Defaults to `below`, which is right for a toolbar at the top of a page.
   * The slide deck's controls sit at the *bottom* of a 100dvh grid, where a
   * panel opening downward is entirely off-screen — there is no scrolling a
   * deck, so it is not merely awkward, it is invisible.
   */
  placement?: 'below' | 'above';
  /** Extra classes for the trigger button. */
  triggerClassName?: string;
  className?: string | undefined;
}

export function Popover({
  trigger,
  label,
  children,
  align = 'right',
  placement = 'below',
  triggerClassName,
  className,
}: PopoverProps) {
  const group = useContext(PopoverGroupContext);
  const id = useId();
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  // Standalone fallback so a Popover still works outside a group — used in
  // tests and wherever exactly one popover exists.
  const [soloOpen, setSoloOpen] = useState(false);

  const open = group ? group.openId === id : soloOpen;
  const wasOpen = useRef(open);

  useEffect(() => {
    // Returning focus to the trigger on close is the half of the interaction
    // people notice only when it is missing: without it, dismissing a panel
    // with Escape drops keyboard focus back to the document body.
    if (wasOpen.current && !open) buttonRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const onToggle = (): void => {
    if (group) group.toggle(id);
    else setSoloOpen((v) => !v);
  };

  return (
    <div className={styles['popoverAnchor']}>
      <button
        ref={buttonRef}
        type="button"
        className={cx(styles['tbtn'], open && styles['tbtnOpen'], triggerClassName)}
        aria-expanded={open}
        aria-controls={id}
        aria-label={label}
        onClick={onToggle}
      >
        {trigger}
      </button>
      {/* Kept in the DOM and hidden, rather than unmounted: `aria-controls`
          pointing at a non-existent id is meaningless to a screen reader, and
          `hidden` already removes it from the accessibility tree. */}
      <div
        id={id}
        role="group"
        aria-label={label}
        hidden={!open}
        className={cx(
          styles['popover'],
          align === 'left' && styles['popoverLeft'],
          placement === 'above' && styles['popoverAbove'],
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
