/**
 * The run-over-run trend card, shared by every export that has a history.
 *
 * Port of `html_theme.sparkline_card`, and the reason that helper can eventually
 * go: three exporters each called it with their own title and their own store
 * rows, and each one re-derived the same domain padding. The padding is the part
 * worth keeping — a series that moves between 7 and 9 cards drawn against a
 * fixed 0-based domain is a flat line, which reads as "nothing changed".
 *
 * **The padding is proportional, unlike the Python original's flat ±8.** Eight
 * was chosen for confidence percentages, where a series that moves 71→78 needs
 * room around it; applied to a count that moves 2→6 it draws a 4-unit swing on a
 * 14-unit domain, which is the flat line the padding exists to prevent. A share
 * of the series' own range does the right thing for both.
 *
 * The bounds come from the payload, defaulting to `floor: 0` — every series
 * these reports carry is a count or a percentage, and a padded minimum of -1
 * tickets is not a fact about anything.
 */

import { Card, Sparkline, sparklineDomain } from '../../design/primitives';
import type { Tone } from '../../design/tone';
import type { Trend } from '../boot';
import styles from './reports.module.css';

/** Renders nothing below two points — one run is not a trend. */
export function TrendCard({ trend, endTone }: { trend: Trend | null; endTone?: Tone }) {
  if (!trend || trend.points.length < 2) return null;

  const values = trend.points.map(([, value]) => value);
  // At least 1, so a genuinely flat series still gets a domain to sit in the
  // middle of rather than a zero-height one.
  const pad = Math.max(1, (Math.max(...values) - Math.min(...values)) * 0.25);
  const { vmin, vmax } = sparklineDomain(values, {
    pad,
    floor: trend.floor ?? 0,
    ...(trend.ceiling === undefined ? {} : { ceiling: trend.ceiling }),
  });
  const first = trend.points[0] as [string, number];
  const last = trend.points[trend.points.length - 1] as [string, number];

  return (
    <Card title={trend.title} className={styles['trend']}>
      <Sparkline
        values={values}
        title={trend.label}
        vmin={vmin}
        vmax={vmax}
        {...(endTone ? { endTone } : {})}
        startLabel={first[0]}
        endLabel={last[0]}
      />
    </Card>
  );
}
