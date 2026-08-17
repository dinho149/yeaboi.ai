/**
 * The activity-kind vocabulary: label, tone, group, and glyph.
 *
 * `kind` is engine-produced and unvalidated, so every lookup here degrades —
 * an unknown kind renders the muted fallback ("ref", a diamond) rather than
 * failing a build; the server can add a kind without a coordinated release.
 *
 * Icons are inline SVG on `currentColor` and always `aria-hidden`: the word
 * (chip label, tooltip, aria-label) travels beside them, and every group has
 * its own *shape*, so identity is never colour alone.
 */

import type { ReactNode } from 'react';

import type { Tone } from '../../design/tone';

/** `kind` is engine-produced, not validated — an unknown one degrades to muted. */
export const KIND_META: Record<string, { label: string; tone: Tone }> = {
  commit: { label: 'commit', tone: 'accent2' },
  pr: { label: 'PR', tone: 'accent' },
  review: { label: 'review', tone: 'info' },
  comment: { label: 'comment', tone: 'low' },
  issue: { label: 'ticket', tone: 'accent' },
  update: { label: 'ticket', tone: 'accent' },
  work_item: { label: 'ticket', tone: 'accent' },
  ticket: { label: 'ticket', tone: 'accent' },
  wip: { label: 'in progress', tone: 'warn' },
  page: { label: 'doc', tone: 'info' },
  'page-created': { label: 'doc', tone: 'info' },
};

export function kindMeta(kind: string): { label: string; tone: Tone } {
  return KIND_META[kind] ?? { label: kind || 'ref', tone: 'low' };
}

/** The eight glyph families the timeline and legend draw with. */
export type KindGroup = 'commit' | 'pr' | 'review' | 'comment' | 'ticket' | 'doc' | 'wip' | 'ref';

const KIND_GROUP: Record<string, KindGroup> = {
  commit: 'commit',
  pr: 'pr',
  review: 'review',
  comment: 'comment',
  issue: 'ticket',
  update: 'ticket',
  work_item: 'ticket',
  ticket: 'ticket',
  page: 'doc',
  'page-created': 'doc',
  wip: 'wip',
};

/** Glyph family for an engine kind; anything unrecognised is a plain `ref`. */
export function kindGroup(kind: string): KindGroup {
  return KIND_GROUP[kind] ?? 'ref';
}

/** One `<path>`/shape set per group — distinct silhouettes, not recolours. */
const GLYPHS: Record<KindGroup, ReactNode> = {
  // A commit node on its branch line.
  commit: (
    <>
      <circle cx="6" cy="6" r="2.4" />
      <path d="M0.8 6h2.8M8.4 6h2.8" />
    </>
  ),
  // The merge arrow: branch node curving into the target branch.
  pr: (
    <>
      <circle cx="3.2" cy="2.8" r="1.7" />
      <circle cx="3.2" cy="9.2" r="1.7" />
      <circle cx="9" cy="9.2" r="1.7" />
      <path d="M3.2 4.5v3M9 7.5V6.8A3.4 3.4 0 0 0 5.6 3.4h-0.5" />
    </>
  ),
  // A magnifier — the review looks the work over.
  review: (
    <>
      <circle cx="5" cy="5" r="3.2" />
      <path d="M7.4 7.4l3.2 3.2" />
    </>
  ),
  // A speech bubble.
  comment: <path d="M1.5 2.2h9v5.6H5.6L3 10V7.8H1.5z" />,
  // A tag, hole and all.
  ticket: (
    <>
      <path d="M1.5 1.5h4.2l4.8 4.8-4.2 4.2-4.8-4.8z" />
      <circle cx="3.8" cy="3.8" r="0.8" />
    </>
  ),
  // A page with a folded corner.
  doc: (
    <>
      <path d="M2.5 1h4.6l2.4 2.4V11H2.5z" />
      <path d="M7.1 1v2.4h2.4" />
    </>
  ),
  // A clock face — carried work, still on the clock.
  wip: (
    <>
      <circle cx="6" cy="6" r="4.4" />
      <path d="M6 3.4V6l1.9 1.3" />
    </>
  ),
  // The plain diamond fallback.
  ref: <path d="M6 1.4L10.6 6 6 10.6 1.4 6z" />,
};

/**
 * The glyph for an activity kind. Colour comes from the wrapper's
 * `currentColor`; pair it with the kind's word — never the icon alone.
 */
export function KindIcon({ kind, size = 12 }: { kind: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {GLYPHS[kindGroup(kind)]}
    </svg>
  );
}
