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
 */

import { useEffect, useId, useRef, useState } from 'react';

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
  const root = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const id = useId();

  const items = value && !options.includes(value) ? [value, ...options] : [...options];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, items.indexOf(value)));
    const away = (event: PointerEvent): void => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', away);
    return () => window.removeEventListener('pointerdown', away);
    // The menu's contents are fixed while it is open; reopening re-runs this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

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

      {open ? (
        <ul className={styles['ddMenu']} role="listbox" aria-label={label}>
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
        </ul>
      ) : null}
    </div>
  );
}
