/**
 * The swatch key under a segmented bar or chart.
 *
 * Port of `html_theme.legend`, plus {@link countedSegments}, which is the shared
 * "turn (label, count) pairs into a bar and its key" logic lifted out of
 * `html_theme.counted_segment_bar` so a chart and its legend cannot disagree
 * about which colour meant which thing.
 */

import { cx } from '../../runtime/cx';
import { SERIES_TONES, toneVar, type Tone } from '../tone';
import type { Segment } from './Bars';
import styles from './primitives.module.css';

export interface LegendItem {
  label: string;
  tone: Tone;
  /** Rendered in the mono voice beside the label. */
  count?: number;
}

export function Legend({ items, className }: { items: readonly LegendItem[]; className?: string }) {
  if (!items.length) return null;
  return (
    <div className={cx(styles['legend'], className)}>
      {items.map((item) => (
        <span key={`${item.label}-${item.tone}`}>
          {/* aria-hidden: the colour is decoration, the label already says what
              this is, and announcing "image" before every entry is noise. */}
          <i className={styles['legendSwatch']} style={{ background: toneVar(item.tone) }} aria-hidden="true" />
          {item.label}
          {item.count === undefined ? null : <span className={styles['legendCount']}> {item.count}</span>}
        </span>
      ))}
    </div>
  );
}

/**
 * Turn `(label, count)` pairs into matched {@link Segment}s and {@link LegendItem}s.
 *
 * Sorted descending, zero/negative dropped, and anything past the palette folded
 * into a single muted "other" bucket rather than inventing hues no theme
 * defines. Both outputs are built from the same zip, which is the point.
 */
export function countedSegments(
  counts: readonly (readonly [string, number])[],
  { palette = SERIES_TONES, overflowLabel = 'other' }: { palette?: readonly Tone[]; overflowLabel?: string } = {}
): { segments: Segment[]; legend: LegendItem[] } {
  const pairs = counts.filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  if (!pairs.length) return { segments: [], legend: [] };

  let folded: (readonly [string, number])[] = pairs;
  if (pairs.length > palette.length + 1) {
    const head = pairs.slice(0, palette.length);
    const tail = pairs.slice(palette.length);
    folded = [...head, [overflowLabel, tail.reduce((sum, [, n]) => sum + n, 0)] as const];
  }

  const tones: Tone[] = [...palette, 'muted'];
  return {
    segments: folded.map(([label, value], i) => ({ value, tone: tones[i] as Tone, label })),
    legend: folded.map(([label, count], i) => ({ label, count, tone: tones[i] as Tone })),
  };
}
