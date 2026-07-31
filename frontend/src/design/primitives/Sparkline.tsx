/**
 * A trend line: inline SVG, line + soft area fill + end dot.
 *
 * Port of `html_theme.sparkline_svg`. Hand-built SVG rather than a charting
 * library or a rendered PNG for one reason that governs everything on these
 * pages: the colours are `var(--…)` custom properties, so the chart recolours
 * with the theme and prints correctly. A raster image cannot do that, and a
 * charting library would blow past the whole bundle budget for one 48px line.
 *
 * The value domain is the caller's business. Padding a narrow band is what
 * keeps a series that moves between 71% and 78% from drawing as a flat line —
 * see `sparklineDomain`.
 */

import { cx } from '../../runtime/cx';
import { toneVar, type Tone } from '../tone';
import styles from './primitives.module.css';

const WIDTH = 600;
const HEIGHT = 48;
const PAD = 6;

export interface SparklineProps {
  values: readonly number[];
  /** Accessible description. Required — a chart with no label is invisible to AT. */
  title: string;
  vmin?: number;
  vmax?: number;
  tone?: Tone;
  /** Colour of the final dot, when the latest point should stand out. */
  endTone?: Tone;
  startLabel?: string;
  endLabel?: string;
  className?: string | undefined;
}

/** Pad a series' own range so a narrow band still reads as a line, not a rule. */
export function sparklineDomain(
  values: readonly number[],
  { pad = 8, floor, ceiling }: { pad?: number; floor?: number; ceiling?: number } = {}
): { vmin: number; vmax: number } {
  const lo = Math.min(...values) - pad;
  const hi = Math.max(...values) + pad;
  return {
    vmin: floor === undefined ? lo : Math.max(floor, lo),
    vmax: ceiling === undefined ? hi : Math.min(ceiling, hi),
  };
}

/** Renders `null` below two points — one point is not a trend. */
export function Sparkline({
  values,
  title,
  vmin,
  vmax,
  tone = 'accent',
  endTone,
  startLabel,
  endLabel,
  className,
}: SparklineProps) {
  if (values.length < 2) return null;

  const lo = vmin ?? Math.min(...values);
  const hi = vmax ?? Math.max(...values);
  const span = hi - lo;

  const points = values.map((value, i) => {
    const x = PAD + (i / (values.length - 1)) * (WIDTH - 2 * PAD);
    // A flat series has zero span; drawing it down the middle is honest, and
    // dividing by zero would put NaN into the path and blank the chart.
    const frac = span <= 0 ? 0.5 : (Math.min(Math.max(value, lo), hi) - lo) / span;
    return [x, PAD + (1 - frac) * (HEIGHT - 2 * PAD)] as const;
  });

  const first = points[0] as readonly [number, number];
  const last = points[points.length - 1] as readonly [number, number];
  const baseline = HEIGHT - PAD;
  const poly = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = [
    `M ${first[0].toFixed(1)},${first[1].toFixed(1)}`,
    ...points.slice(1).map(([x, y]) => `L ${x.toFixed(1)},${y.toFixed(1)}`),
    `L ${last[0].toFixed(1)},${baseline.toFixed(1)}`,
    `L ${first[0].toFixed(1)},${baseline.toFixed(1)}`,
    'Z',
  ].join(' ');

  return (
    <div className={cx(styles['sparkWrap'], className)}>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        // The drawing stretches to the container width; vector-effect below
        // keeps the stroke and the dot ring crisp while it does.
        preserveAspectRatio="none"
        role="img"
        aria-label={title}
      >
        <title>{title}</title>
        <path d={area} fill={toneVar(tone)} fill-opacity="0.12" />
        <polyline
          points={poly}
          fill="none"
          stroke={toneVar(tone)}
          stroke-width="2"
          stroke-linejoin="round"
          stroke-linecap="round"
          vector-effect="non-scaling-stroke"
        />
        <circle
          cx={last[0].toFixed(1)}
          cy={last[1].toFixed(1)}
          r="4"
          fill={toneVar(endTone ?? tone)}
          stroke="var(--panel)"
          stroke-width="2"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      {startLabel || endLabel ? (
        <div className={styles['sparkLabels']}>
          <span>{startLabel}</span>
          <span>{endLabel}</span>
        </div>
      ) : null}
    </div>
  );
}
