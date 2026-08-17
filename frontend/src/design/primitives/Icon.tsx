/**
 * The icon set.
 *
 * Vendored Lucide (ISC, v1.31.0) — the path data only, no runtime dependency.
 * Three constraints decide this: bundles must be self-contained (no CDN, no
 * icon font, no dynamic import), exports open over `file://`, and the poker and
 * retro bundles carry every byte they reference. A package would add a
 * dependency and a build-time tree-shake to save nothing over 40 inline paths.
 *
 * To add one: fetch `https://unpkg.com/lucide-static/icons/<name>.svg`, drop
 * the `<svg>` wrapper, and add the inner markup below.
 *
 * The grid is 24x24 with a 2px round-capped stroke, which is why the default
 * `stroke-width` thins as the icon shrinks — a 2px stroke on a 14px icon reads
 * as a blob.
 *
 * The default is derived from the size rather than picked, so that the painted
 * stroke is a whole CSS pixel: the grid is scaled by `size / 24`, and a weight
 * that does not survive that scaling lands between pixels and reads as a
 * slightly out-of-focus icon at every size but one.
 */

import { createElement, type ReactElement } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export type IconName = keyof typeof PATHS | keyof typeof PATHS_16;

const PATHS = {
  'circle-help': '<circle cx="12" cy="12" r="10" /> <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /> <path d="M12 17h.01" />',
  'coffee': '<path d="M10 2v2" /> <path d="M14 2v2" /> <path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1" /> <path d="M6 2v2" />',
  'copy': '<rect width="14" height="14" x="8" y="8" rx="2" ry="2" /> <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />',
  'dices': '<rect width="12" height="12" x="2" y="10" rx="2" ry="2" /> <path d="m17.92 14 3.5-3.5a2.24 2.24 0 0 0 0-3l-5-4.92a2.24 2.24 0 0 0-3 0L10 6" /> <path d="M6 18h.01" /> <path d="M10 14h.01" /> <path d="M15 6h.01" /> <path d="M18 9h.01" />',
  'eye': '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" /> <circle cx="12" cy="12" r="3" />',
  'flag': '<path d="M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528" />',
  'panel-left-close': '<rect width="18" height="18" x="3" y="3" rx="2" /> <path d="M9 3v18" /> <path d="m16 15-3-3 3-3" />',
  'pause': '<rect x="14" y="3" width="5" height="18" rx="1" /> <rect x="5" y="3" width="5" height="18" rx="1" />',
  'qr-code': '<rect width="5" height="5" x="3" y="3" rx="1" /> <rect width="5" height="5" x="16" y="3" rx="1" /> <rect width="5" height="5" x="3" y="16" rx="1" /> <path d="M21 16h-3a2 2 0 0 0-2 2v3" /> <path d="M21 21v.01" /> <path d="M12 7v3a2 2 0 0 1-2 2H7" /> <path d="M3 12h.01" /> <path d="M12 3h.01" /> <path d="M12 16v.01" /> <path d="M16 12h1" /> <path d="M21 12v.01" /> <path d="M12 21v-1" />',
  'rotate-ccw': '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /> <path d="M3 3v5h5" />',
  'share-2': '<circle cx="18" cy="5" r="3" /> <circle cx="6" cy="12" r="3" /> <circle cx="18" cy="19" r="3" /> <line x1="8.59" x2="15.42" y1="13.51" y2="17.49" /> <line x1="15.41" x2="8.59" y1="6.51" y2="10.49" />',
  'triangle-alert': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /> <path d="M12 9v4" /> <path d="M12 17h.01" />',
  'undo-2': '<path d="M9 14 4 9l5-5" /> <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" />',
  'wifi-off': '<path d="M12 20h.01" /> <path d="M8.5 16.429a5 5 0 0 1 7 0" /> <path d="M5 12.859a10 10 0 0 1 5.17-2.69" /> <path d="M19 12.859a10 10 0 0 0-2.007-1.523" /> <path d="M2 8.82a15 15 0 0 1 4.177-2.643" /> <path d="M22 8.82a15 15 0 0 0-11.288-3.764" /> <path d="m2 2 20 20" />'
} as const;

/**
 * The board's own glyphs, drawn here rather than vendored.
 *
 * Two reasons, and the first is arithmetic. A 24-unit grid rendered at 16px is
 * scaled by two thirds, so a coordinate at 11 lands at 7.333 — every
 * axis-aligned edge in the icon falls between pixels and the whole set reads as
 * slightly out of focus no matter what the stroke weighs. These are on a 16 grid
 * with every straight edge on a half unit, so at 16px a one-unit stroke fills
 * exactly one pixel row.
 *
 * The second is that they move. Each part carries a `data-part`, which is what
 * primitives.module.css animates — a shackle that lifts, a hand that sweeps —
 * and naming them here keeps the drawing and the motion describing the same
 * thing. The attribute rides through the parser with every other one.
 */
const PATHS_16 = {
  'arrow-left': '<path data-part="shaft" d="M13.5 8.5h-11" /> <path data-part="head" d="M6.5 4 2 8.5 6.5 13" />',
  'arrow-right': '<path data-part="shaft" d="M2.5 8.5h11" /> <path data-part="head" d="M9.5 4 14 8.5 9.5 13" />',
  'check': '<path data-part="tick" d="m3 8.5 3.5 3.5 6.5-8" />',
  'chevron-down': '<path data-part="v" d="M3.5 5.5 8 10l4.5-4.5" />',
  'chevron-left': '<path data-part="v" d="M9.5 3.5 5 8l4.5 4.5" />',
  'chevron-right': '<path data-part="v" d="M6.5 3.5 11 8l-4.5 4.5" />',
  'chevron-up': '<path data-part="v" d="M3.5 10.5 8 6l4.5 4.5" />',
  'contrast':
    '<circle data-part="rim" cx="8" cy="8" r="5.5" /> <path data-part="fill" fill="currentColor" stroke="none" d="M8 2.5a5.5 5.5 0 0 1 0 11z" />',
  'lock-open':
    '<rect data-part="body" x="2.5" y="7.5" width="11" height="6" rx="1.5" /> <path data-part="shackle" d="M4.5 7.5V5.5a3.5 3.5 0 0 1 7 0" />',
  'lock': '<rect data-part="body" x="2.5" y="7.5" width="11" height="6" rx="1.5" /> <path data-part="shackle" d="M4.5 7.5V5.5a3.5 3.5 0 0 1 7 0v2" />',
  'mail': '<rect data-part="body" x="1.5" y="3.5" width="13" height="9" rx="1.5" /> <path data-part="flap" d="M1.5 4.5 8 9.5l6.5-5" />',
  'music':
    '<path data-part="staff" d="M6.5 12.5v-9l7-1v9" /> <circle data-part="low" cx="4.75" cy="12.5" r="1.75" /> <circle data-part="high" cx="11.75" cy="10.5" r="1.75" />',
  'timer':
    '<path data-part="crown" d="M6.5 1.5h3" /> <circle data-part="case" cx="8" cy="9.5" r="5.5" /> <path data-part="hand" d="M8 9.5 10.5 7" />',
  /* Dots rather than strokes: a grip is a texture, and six 2px squares survive
     any scale a stroke of the same weight would blur. */
  'grip':
    '<circle data-part="dot" fill="currentColor" stroke="none" cx="6" cy="4" r="1" /> <circle data-part="dot" fill="currentColor" stroke="none" cx="10" cy="4" r="1" /> <circle data-part="dot" fill="currentColor" stroke="none" cx="6" cy="8" r="1" /> <circle data-part="dot" fill="currentColor" stroke="none" cx="10" cy="8" r="1" /> <circle data-part="dot" fill="currentColor" stroke="none" cx="6" cy="12" r="1" /> <circle data-part="dot" fill="currentColor" stroke="none" cx="10" cy="12" r="1" />',
  'plus': '<path data-part="h" d="M3 8.5h11" /> <path data-part="v" d="M8.5 3v11" />',
  'smile':
    '<circle data-part="face" cx="8" cy="8" r="6.5" /> <path data-part="mouth" d="M5 8.75a3.2 3.2 0 0 0 6 0" /> <circle data-part="eye" fill="currentColor" stroke="none" cx="5.75" cy="6.25" r="0.85" /> <circle data-part="eye" fill="currentColor" stroke="none" cx="10.25" cy="6.25" r="0.85" />',
  'trash': '<path data-part="lid" d="M2.5 4.5h11" /> <path data-part="grab" d="M6 4.5v-2h4v2" /> <path data-part="can" d="M4 4.5v9a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-9" />',
  'undo':
    '<path data-part="head" d="M6 3 2.5 6.5 6 10" /> <path data-part="arc" d="M2.5 6.5h6a4 4 0 0 1 0 8H5.5" />',
  'user':
    '<circle data-part="head" cx="8" cy="5.5" r="2.5" /> <path data-part="body" d="M2.5 14.5a5.5 5.5 0 0 1 11 0" />',
  'external-link':
    '<path data-part="frame" d="M13.5 8.5v4a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 4 2.5h4" /> <path data-part="out" d="M10.5 2.5h3v3" /> <path data-part="out" d="m8 8 5.5-5.5" />',
  'mic':
    '<rect data-part="capsule" x="5.5" y="1.5" width="5" height="7" rx="2.5" /> <path data-part="arc" d="M3.5 7.5v.5a4.5 4.5 0 0 0 9 0v-.5" /> <path data-part="stand" d="M8 13v1.5" /> <path data-part="foot" d="M5.5 14.5h5" />',
  'megaphone':
    '<path data-part="horn" d="M4.5 6 13 2.5v11L4.5 10z" /> <path data-part="cone" d="M4.5 6h-1a1.5 1.5 0 0 0 0 4h1z" /> <path data-part="tail" d="M6.5 10.5v2a1.5 1.5 0 0 0 3 0v-1.2" />',
  'menu': '<path data-part="bar" d="M2.5 4.5h11" /> <path data-part="bar" d="M2.5 8.5h11" /> <path data-part="bar" d="M2.5 12.5h11" />',
  'pen-line':
    '<path data-part="nib" d="m11 2.5 2.5 2.5-6.5 6.5-3.5 1 1-3.5z" /> <path data-part="rule" d="M2.5 14.5h11" />',
  'pencil':
    '<path data-part="nib" d="m11 2.5 2.5 2.5-8 8-3.5 1 1-3.5z" /> <path data-part="ferrule" d="m9.5 4 2.5 2.5" />',
  'play': '<path data-part="tri" fill="currentColor" d="M5.5 3.5 13 8l-7.5 4.5z" />',
  'sparkles':
    '<path data-part="star" d="M6.5 1.5 8 5.5l4 1.5-4 1.5-1.5 4-1.5-4-4-1.5 4-1.5z" /> <path data-part="spark" d="M12.5 1.5 13 3l1.5.5-1.5.5-.5 1.5-.5-1.5L10.5 3.5 12 3z" /> <path data-part="spark" d="M12.5 9.5 13 11l1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5z" />',
  'swords':
    '<path data-part="a" d="M3.5 3 10.5 10" /> <path data-part="a" d="m9.5 13.5 4-4" /> <path data-part="b" d="M12.5 3 5.5 10" /> <path data-part="b" d="m2.5 9.5 4 4" />',
  'users':
    '<circle data-part="head" cx="6" cy="5.5" r="2.5" /> <path data-part="front" d="M1.5 14a4.5 4.5 0 0 1 9 0" /> <path data-part="back" d="M11 3.6a2.5 2.5 0 0 1 0 3.8" /> <path data-part="back" d="M14.5 14a4.5 4.5 0 0 0-3-4.2" />',
  'x': '<path data-part="s" d="m4 4 8 8" /> <path data-part="s" d="M12 4 4 12" />',
} as const;

export interface IconProps {
  name: IconName;
  /** Rendered size in px. The stroke thins below 20 to hold its weight. */
  size?: number;
  /** Overrides the size-derived stroke. */
  strokeWidth?: number;
  className?: string | undefined;
}

const SHAPE = /<(path|circle|line|polyline|polygon|rect|ellipse)\s+([^>]*?)\/>/g;
const ATTR = /([a-zA-Z][\w-]*)="([^"]*)"/g;

const parsed = new Map<IconName, ReactElement[]>();

/**
 * The glyph body as real elements.
 *
 * These are vendored Lucide shapes — a fixed, author-controlled table, not
 * content — but they are still parsed rather than injected: `innerHTML` is
 * banned across this front end, and one exemption is how that rule stops being
 * a rule. Parsed once per icon and cached.
 */
/** The grid a name is drawn on. 16 is hand-drawn here, 24 is vendored Lucide. */
function grid(name: IconName): 16 | 24 {
  return name in PATHS_16 ? 16 : 24;
}

function shapes(name: IconName): ReactElement[] {
  const cached = parsed.get(name);
  if (cached) return cached;
  const out: ReactElement[] = [];
  const drawing: string = (PATHS_16 as Record<string, string>)[name] ?? (PATHS as Record<string, string>)[name] ?? '';
  for (const shape of drawing.matchAll(SHAPE)) {
    const tag = shape[1] ?? '';
    const props: Record<string, string> = {};
    for (const attr of (shape[2] ?? '').matchAll(ATTR)) {
      if (attr[1]) props[attr[1]] = attr[2] ?? '';
    }
    if (tag) out.push(createElement(tag, { ...props, key: `${tag}${out.length}` }));
  }
  parsed.set(name, out);
  return out;
}

/** Decorative by default: every icon here sits beside its own label. */
export function Icon({ name, size = 16, strokeWidth, className }: IconProps) {
  const units = grid(name);
  return (
    <svg
      className={cx(styles['icon'], className)}
      data-icon={name}
      width={size}
      height={size}
      viewBox={`0 0 ${units} ${units}`}
      fill="none"
      stroke="currentColor"
      // One painted pixel on the 16 grid at any size, because every straight
      // edge in that set is already on a half unit. The 24 grid is drawn for a
      // 2px stroke and has to thin as it shrinks or it reads as a blob.
      strokeWidth={strokeWidth ?? (units === 16 ? 16 / size : (24 / size) * (size >= 20 ? 2 : 1))}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {shapes(name)}
    </svg>
  );
}
