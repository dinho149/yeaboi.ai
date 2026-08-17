/**
 * A strip of views over one box.
 *
 * The strip stays still. Panels share a single grid cell, so the box is as tall
 * as the tallest of them and switching cannot move the tabs you are clicking —
 * stacked, a taller panel pushes everything down as you select it and the strip
 * slides out from under the pointer.
 *
 * Below two views there is no choice to offer, so the caller renders its own
 * heading instead: a lone tab is a label that looks pressable.
 */

import { type ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './board.module.css';

export interface TabsProps<Id extends string> {
  /** In the order they are shown. */
  tabs: readonly { id: Id; label: string }[];
  current: Id;
  onPick(next: Id): void;
  /** Names the strip for assistive tech, and keys the panels' ids. */
  label: string;
  className?: string | undefined;
}

/** The ids a panel and its tab agree on, so the two are wired to each other. */
export function tabIds(label: string, id: string): { tab: string; panel: string } {
  const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  return { tab: `${slug}-tab-${id}`, panel: `${slug}-panel-${id}` };
}

export function Tabs<Id extends string>({ tabs, current, onPick, label, className }: TabsProps<Id>) {
  return (
    <div className={cx(styles['tabs'], className)} role="tablist" aria-label={label}>
      {tabs.map((tab) => {
        const ids = tabIds(label, tab.id);
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={ids.tab}
            aria-selected={current === tab.id}
            aria-controls={ids.panel}
            className={cx(styles['tab'], current === tab.id && styles['tabOn'])}
            onClick={() => onPick(tab.id)}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export interface PanelsProps {
  children: ReactNode;
  className?: string | undefined;
}

/** The shared cell every panel is drawn in. */
export function Panels({ children, className }: PanelsProps) {
  return <div className={cx(styles['panels'], className)}>{children}</div>;
}

export interface PanelProps {
  id: string;
  label: string;
  showing: boolean;
  /** False when there is only one view, so it is a region rather than a panel. */
  tabbed?: boolean;
  children: ReactNode;
}

export function Panel({ id, label, showing, tabbed = true, children }: PanelProps) {
  const ids = tabIds(label, id);
  return (
    <div
      role={tabbed ? 'tabpanel' : undefined}
      id={ids.panel}
      aria-labelledby={tabbed ? ids.tab : undefined}
      className={cx(!showing && styles['panelOff'])}
    >
      {children}
    </div>
  );
}
