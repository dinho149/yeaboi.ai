/**
 * Last sprint's action items, up for review.
 *
 * ## Why it collapses
 *
 * The old panel sat above the grids at full height for the entire ceremony, so
 * a retro with six carried items started with the board itself below the fold.
 * The panel matters for the first two minutes and is dead weight for the other
 * fifty-eight.
 *
 * It therefore opens expanded, and **auto-collapses once every item has been
 * given a status** — the point at which the review is actually finished, rather
 * than a timer or a guess. Reopening is one click, and the summary line keeps
 * the progress visible while collapsed, so nothing is hidden, only folded.
 *
 * The old panel also had a poll-clobbering bug worth naming: `renderCarried`
 * bailed out entirely if the user's focus was inside a `<select>`, because a
 * re-render every 1.2 s would otherwise close the open dropdown. That froze the
 * whole strip — including everyone else's changes — for as long as one person
 * had a dropdown open. Here the `<select>` is a controlled element that React
 * updates in place, so there is nothing to clobber and nothing to freeze.
 */

import { useEffect, useState } from 'react';

import { Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import { CARRIED_STATUS_LABELS, CARRIED_STATUSES, type CarriedStatuses } from '../types/enums';
import type { RetroCard } from '../types/board';
import styles from './retro.module.css';

/** Tone class per status, so "done" and "not relevant" do not look alike. */
const STATUS_CLASS: Record<CarriedStatuses, string> = {
  pending: 'statusPending',
  done: 'statusDone',
  in_progress: 'statusProgress',
  carried_over: 'statusCarried',
  not_relevant: 'statusMuted',
};

export interface CarriedStripProps {
  items: readonly RetroCard[];
  onSetStatus(itemId: string, status: CarriedStatuses): void;
  locked: boolean;
}

export function CarriedStrip({ items, onSetStatus, locked }: CarriedStripProps) {
  const [open, setOpen] = useState(true);
  // Distinguishes "collapsed because the review finished" from "collapsed
  // because you closed it": without it, reopening a finished strip would snap
  // shut again on the next snapshot.
  const [autoCollapsed, setAutoCollapsed] = useState(false);

  const reviewed = items.filter((item) => item.status && item.status !== 'pending').length;
  const complete = items.length > 0 && reviewed === items.length;

  useEffect(() => {
    if (complete && !autoCollapsed) {
      setOpen(false);
      setAutoCollapsed(true);
    }
  }, [complete, autoCollapsed]);

  if (!items.length) return null;

  return (
    <section className={styles['carried']} aria-labelledby="carried-title">
      <header className={styles['carriedHead']}>
        <button
          type="button"
          className={styles['carriedToggle']}
          aria-expanded={open}
          aria-controls="carried-body"
          onClick={() => setOpen((v) => !v)}
        >
          <span className={cx(styles['chevron'], open && styles['chevronOpen'])}>
            <Icon name="chevron-right" size={14} />
          </span>
          <span id="carried-title">Last retro</span>
        </button>
        <span className={styles['carriedProgress']}>
          {reviewed}/{items.length} reviewed
        </span>
      </header>

      <div id="carried-body" hidden={!open} className={styles['carriedBody']}>
        <ul className={styles['carriedList']}>
          {items.map((item) => {
            const status = (item.status || 'pending') as CarriedStatuses;
            return (
              <li key={item.id} className={cx(styles['carriedItem'], styles[STATUS_CLASS[status]])}>
                <span className={styles['carriedText']}>{item.text}</span>
                <select
                  className={styles['carriedSelect']}
                  value={status}
                  disabled={locked}
                  aria-label={`Status for: ${item.text.slice(0, 60)}`}
                  onChange={(event) =>
                    onSetStatus(item.id, (event.target as HTMLSelectElement).value as CarriedStatuses)
                  }
                >
                  {CARRIED_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {CARRIED_STATUS_LABELS[value]}
                    </option>
                  ))}
                </select>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
