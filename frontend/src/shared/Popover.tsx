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

import { createPortal } from 'react-dom';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
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
  /** When set, panels render *into* this element instead of over the trigger. */
  panelHost: HTMLElement | null;
}

const PopoverGroupContext = createContext<PopoverGroupValue | null>(null);

/**
 * Wraps a toolbar so its popovers are mutually exclusive.
 *
 * Also owns the two global dismissals — a pointer-down outside any panel, and
 * Escape — because both are properties of the *group*, not of any one popover.
 *
 * `panelHost` turns the group inside out: instead of each panel floating over
 * its trigger, every panel renders into that one element. The dock uses it so
 * that opening a tool grows the dock itself, which is something no absolutely
 * positioned panel can do — an out-of-flow box cannot size its container.
 */
export function PopoverGroup({ children, panelHost = null }: { children: ReactNode; panelHost?: HTMLElement | null }) {
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

  const value = useMemo(() => ({ openId, toggle, close, panelHost }), [openId, toggle, close, panelHost]);

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
  triggerClassName?: string | undefined;
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
  const panelRef = useRef<HTMLDivElement | null>(null);
  // What the panel actually resolved to once measured against the viewport.
  const [fit, setFit] = useState<{ above: boolean; left: boolean } | null>(null);
  // Standalone fallback so a Popover still works outside a group — used in
  // tests and wherever exactly one popover exists.
  const [soloOpen, setSoloOpen] = useState(false);

  const open = group ? group.openId === id : soloOpen;
  const host = group?.panelHost ?? null;
  const wasOpen = useRef(open);

  /*
   * Flip against whichever edge it would otherwise run off.
   *
   * Measured on open rather than assumed from the prop: the same control moves
   * — the dock travels three walls — so a placement that is right at the
   * bottom-right is wrong once it is at the top-left. The prop stays as the
   * preference; this only overrides it when the preferred side does not fit.
   */
  useLayoutEffect(() => {
    // A docked panel is in flow inside its host, so there is no edge to flip
    // against and nothing to measure.
    if (!open || host) {
      setFit(null);
      return;
    }
    const panel = panelRef.current;
    const button = buttonRef.current;
    if (!panel || !button) return;
    const anchor = button.getBoundingClientRect();
    const { width, height } = panel.getBoundingClientRect();
    const room = { above: anchor.top, below: window.innerHeight - anchor.bottom };
    const wantAbove = placement === 'above';
    const above = wantAbove ? room.above >= height || room.above >= room.below : room.below < height && room.above > room.below;
    const wantLeft = align === 'left';
    const left = wantLeft
      ? anchor.left + width <= window.innerWidth || anchor.right - width < 0
      : anchor.right - width < 0;
    setFit({ above, left });
  }, [open, placement, align, host]);

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

  const panel = (
    /* Kept in the DOM and hidden, rather than unmounted: `aria-controls`
       pointing at a non-existent id is meaningless to a screen reader, and
       `hidden` already removes it from the accessibility tree. */
    <div
      ref={panelRef}
      id={id}
      role="group"
      aria-label={label}
      hidden={!open}
      className={cx(
        styles['popover'],
        host && styles['popoverDocked'],
        !host && (fit ? fit.left : align === 'left') && styles['popoverLeft'],
        !host && (fit ? fit.above : placement === 'above') && styles['popoverAbove'],
        className,
      )}
    >
      {children}
    </div>
  );

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
      {host ? createPortal(panel, host) : panel}
    </div>
  );
}
