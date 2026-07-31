/**
 * Horizontal bars: a single filled track, and a proportional segmented one.
 *
 * Ports of `html_theme.stat_bar` and `html_theme.segment_bar`. Pure CSS rather
 * than SVG — they recolour with the theme, print correctly, and cost nothing.
 *
 * Both carry `role="img"` plus a label. A bar is a picture of a number, and
 * without that a screen reader reads a run of empty `<i>` elements or, worse,
 * nothing at all.
 */

import { cx } from '../../runtime/cx';
import { toneVar, type Tone } from '../tone';
import styles from './primitives.module.css';

export interface StatBarProps {
  /** Fill percentage. Clamped to 0–100 — callers pass ratios that can overshoot. */
  pct: number;
  tone?: Tone;
  /** Accessible description, e.g. "capacity 34 of 40 points". */
  label: string;
  className?: string | undefined;
}

export function StatBar({ pct, tone = 'accent', label, className }: StatBarProps) {
  const width = Math.max(0, Math.min(100, pct));
  return (
    <div
      className={cx(styles['bar'], className)}
      role="img"
      aria-label={label}
      title={label}
    >
      <div className={styles['barFill']} style={{ width: `${width}%`, background: toneVar(tone) }} />
    </div>
  );
}

export interface Segment {
  value: number;
  tone: Tone;
  label?: string;
}

export interface SegmentBarProps {
  segments: readonly Segment[];
  label: string;
  /**
   * Scale of the whole track, 0–100. How a caller normalises several bars
   * against a shared maximum so their lengths are comparable.
   */
  widthPct?: number;
  className?: string | undefined;
}

/**
 * A proportional stacked bar. Renders `null` when nothing positive remains —
 * an empty 10px track reads as "zero of something" rather than "no data".
 */
export function SegmentBar({ segments, label, widthPct = 100, className }: SegmentBarProps) {
  const kept = segments.filter((s) => s.value > 0);
  const total = kept.reduce((sum, s) => sum + s.value, 0);
  if (total <= 0) return null;
  const width = Math.max(0, Math.min(100, widthPct));

  return (
    <div
      className={cx(styles['segTrack'], className)}
      role="img"
      aria-label={label}
      style={{ width: `${width}%` }}
    >
      {kept.map((segment, index) => (
        <i
          // Index keys are correct here and only here: segments have no identity
          // of their own, the list is rebuilt wholesale from a fresh count, and
          // reordering carries no state to lose.
          key={`${segment.tone}-${index}`}
          style={{ flex: `0 0 ${((segment.value / total) * 100).toFixed(1)}%`, background: toneVar(segment.tone) }}
        />
      ))}
    </div>
  );
}
