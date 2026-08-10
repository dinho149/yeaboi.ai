/**
 * A tab strip.
 *
 * Implements the WAI-ARIA tabs pattern, of which the part that is always
 * missing is arrow-key navigation: tabs are a *composite* widget, so Tab moves
 * into and out of the strip while Left/Right move between tabs. Getting this
 * wrong means a keyboard user has to press Tab once per tab to cross the strip.
 *
 * Roving tabindex rather than `aria-activedescendant`, because focus genuinely
 * moves and the browser then handles scroll-into-view for free.
 */

import type { ComponentChildren } from 'preact';
import { useRef } from 'preact/hooks';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface TabItem {
  id: string;
  label: string;
}

export interface TabsProps {
  items: TabItem[];
  active: string;
  onChange: (id: string) => void;
  /** Names the strip for assistive tech, e.g. "Project sections". */
  label: string;
  className?: string | undefined;
}

export function Tabs({ items, active, onChange, label, className }: TabsProps) {
  const strip = useRef<HTMLDivElement>(null);

  function onKeyDown(event: KeyboardEvent) {
    const index = items.findIndex((item) => item.id === active);
    if (index < 0) return;
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % items.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    else return;
    event.preventDefault();
    const target = items[next];
    if (!target) return;
    onChange(target.id);
    // Move real focus with the selection — without this the roving tabindex is
    // decorative and the keyboard user is still parked on the old tab.
    strip.current?.querySelector<HTMLElement>(`[data-tab="${target.id}"]`)?.focus();
  }

  return (
    <div ref={strip} className={cx(styles['tabs'], className)} role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {items.map((item) => {
        const selected = item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            data-tab={item.id}
            id={`tab-${item.id}`}
            aria-selected={selected}
            aria-controls={`panel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            className={cx(styles['tab'], selected && styles['tabActive'])}
            onClick={() => onChange(item.id)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, active, children }: { id: string; active: string; children: ComponentChildren }) {
  if (id !== active) return null;
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={0}>
      {children}
    </div>
  );
}
