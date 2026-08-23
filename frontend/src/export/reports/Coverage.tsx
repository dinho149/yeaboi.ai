/**
 * What each evidence source contributed to a report — the dot, and the word.
 *
 * Two modes carry the same four-state vocabulary now (standup's per-category
 * coverage, performance's per-source coverage), so the dot lives here rather
 * than in whichever component drew it first. The status word ALWAYS renders
 * beside the dot: colour alone is not a signal every reader can receive, and a
 * page whose only way of saying "nobody looked" is a grey circle has not said
 * it.
 *
 * An unrecognised status degrades to the muted dot with its own word intact —
 * the engine produces these, so a new state must render, not fail a build.
 */

import { toneVar, type Tone } from '../../design/tone';
import type { CoverageStates } from '../../types/enums';
import styles from './reports.module.css';

/** Coverage statuses → tones. Keyed by the generated vocabulary, so a state added
 *  in Python fails the typecheck here rather than rendering an unexplained grey dot. */
export const COVERAGE_TONE: Record<CoverageStates, Tone> = {
  covered: 'ok',
  partial: 'warn',
  failed: 'danger',
  not_configured: 'low',
};

export function coverageTone(status: string): Tone {
  // The engine produces these words; an unrecognised one must render muted, not
  // fail a build — so the lookup widens back to string here.
  return (COVERAGE_TONE as Record<string, Tone>)[status] ?? 'low';
}

/** One source's contribution: what it is called, how it went, and optionally why. */
export interface CoverageEntry {
  label: string;
  status: string;
  /** The honest sentence — "12 runs, none named this engineer." Title text only;
   *  a caller that needs it read aloud renders it as its own row as well. */
  detail?: string;
}

export function CoverageDots({ items }: { items: readonly CoverageEntry[] }) {
  if (!items.length) return null;
  return (
    <>
      {items.map((item) => (
        <span
          key={item.label}
          className={styles['coverage']}
          {...(item.detail ? { title: item.detail } : {})}
        >
          <i
            className={styles['dot']}
            style={{ background: toneVar(coverageTone(item.status)) }}
            aria-hidden="true"
          />
          {item.label} <span className={styles['dim']}>{item.status.replace(/_/g, ' ')}</span>
        </span>
      ))}
    </>
  );
}
