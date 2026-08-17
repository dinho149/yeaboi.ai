/**
 * A picker that reads as the value it replaces.
 *
 * Not a `<select>`: a native one draws the operating system's own widget, which
 * cannot be typeset with the rest of the page and looks like nothing else on
 * it. This is a button plus a listbox, so the closed state is just the text.
 *
 * The current value is always offered even when the caller's options do not
 * contain it — a tracker that answers with the transitions an issue can reach
 * leaves out the one it is already in.
 *
 * The menu is portalled to the body and positioned `fixed`. It has to be: the
 * dock's drawer clips its contents (that is how the notch animates its own
 * size), so a menu in flow is cut off at the panel's edge. Being out of the
 * document flow, it also has to decide for itself whether it opens down or up
 * — measured against the viewport, since a picker at the bottom of the screen
 * has no room below it.
 */

import { createPortal } from 'react-dom';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import { cx } from '../../runtime/cx';
import { Icon } from './Icon';
import styles from './primitives.module.css';

export interface DropdownProps {
  /** Accessible name — the property this picks a value for. */
  label: string;
  value: string;
  options: readonly string[];
  onChange(next: string): void;
  /** Shown in place of an empty value. */
  placeholder?: string;
  className?: string | undefined;
}

export function Dropdown({ label, value, options, onChange, placeholder = '—', className }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [rect, setRect] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLUListElement | null>(null);
  const id = useId();

  const items = value && !options.includes(value) ? [value, ...options] : [...options];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, items.indexOf(value)));
    const away = (event: PointerEvent): void => {
      const target = event.target as Node;
      if (root.current?.contains(target) || menu.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener('pointerdown', away);
    return () => window.removeEventListener('pointerdown', away);
    // The menu's contents are fixed while it is open; reopening re-runs this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Placed against the viewport, before paint. Opening downward is preferred
  // and only given up when the list would not fit.
  useLayoutEffect(() => {
    if (!open) {
      setRect(null);
      return;
    }
    const anchor = trigger.current?.getBoundingClientRect();
    if (!anchor) return;
    // Plus a margin, so "it just fits" does not mean flush against the edge.
    const wanted = Math.min(240, items.length * 36 + 16) + 12;
    const below = window.innerHeight - anchor.bottom;
    const place = { left: anchor.left, width: anchor.width };
    setRect(
      below < wanted && anchor.top > below
        ? { ...place, bottom: window.innerHeight - anchor.top + 6 }
        : { ...place, top: anchor.bottom + 6 },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // The menu is as wide as its longest option, which the anchor's width does not
  // predict — a picker near the right edge would run off it. Measured after the
  // first paint and pulled back in; the shift converges in one pass because a
  // menu that fits does not move.
  useLayoutEffect(() => {
    if (!open || !rect || !menu.current) return;
    const box = menu.current.getBoundingClientRect();
    const spill = box.right - (window.innerWidth - 8);
    if (spill > 0) setRect((current) => (current ? { ...current, left: Math.max(8, current.left - spill) } : current));
  }, [open, rect]);

  const choose = (next: string): void => {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && open) {
      // Swallowed, or the surface behind takes it as "leave edit mode" while
      // the only thing the user meant to close was this menu.
      event.stopPropagation();
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((i) => (items.length ? (i + step + items.length) % items.length : 0));
      return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault();
      const picked = items[active];
      if (picked !== undefined) choose(picked);
    }
  };

  return (
    <div ref={root} className={cx(styles['dd'], className)}>
      <button
        ref={trigger}
        type="button"
        id={`${id}-trigger`}
        className={cx(styles['ddTrigger'], open && styles['ddTriggerOpen'])}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={onKeyDown}
      >
        <span className={cx(styles['ddValue'], !value && styles['ddEmpty'])}>{value || placeholder}</span>
        <Icon name="chevron-down" size={12} className={styles['ddCaret']} />
      </button>

      {open && rect
        ? createPortal(
        <ul
          ref={menu}
          // The group that owns the surrounding popover closes on any
          // pointerdown outside itself, and this menu is outside everything.
          data-dropdown-menu=""
          className={styles['ddMenu']}
          role="listbox"
          aria-label={label}
          style={{
            left: `${rect.left}px`,
            minWidth: `${rect.width}px`,
            ...(rect.top === undefined ? { bottom: `${rect.bottom}px` } : { top: `${rect.top}px` }),
          }}
        >
          {items.length === 0 ? (
            <li className={styles['ddNone']}>Nothing to pick from.</li>
          ) : (
            items.map((option, index) => (
              <li
                key={option}
                role="option"
                aria-selected={option === value}
                className={cx(styles['ddOpt'], index === active && styles['ddOptActive'])}
                onMouseEnter={() => setActive(index)}
                // Down rather than click: a click fires after the blur that
                // would already have closed the menu under the pointer.
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option);
                }}
              >
                <span>{option}</span>
                {option === value ? <Icon name="check" size={12} /> : null}
              </li>
            ))
          )}
        </ul>,
        document.body,
      )
        : null}
    </div>
  );
}
