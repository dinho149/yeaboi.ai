/**
 * The performance exports: 1:1 prep, 1:1 summary, and the six-month review.
 *
 * One component for all three. That is the point of the payload shape rather
 * than an accident of it — the three artifacts *are* one shape: an engineer, at
 * most one block of free prose, then titled runs of bullets, with the numbers
 * and the items those bullets were written from.
 *
 * The engineer's name is a row in the body rather than the page title alone,
 * with an avatar: these documents get read in a stack, one per person, and the
 * name is the first thing you need to be sure about.
 *
 * Two rules the rest of the file follows:
 *
 * - **No stat carries a tone.** Profile's `Cell.tone` is the one documented
 *   exception to presentation crossing the wire, and it earned that by having
 *   thresholds that are per-column *and* directional. A performance figure has
 *   no such thresholds: whether 62% of changes carrying tests is good is the
 *   reader's judgement about their own team, and a page that paints it red has
 *   taken a position it has no standing to take.
 * - **A section that is empty says which kind of empty it is**, in the same
 *   four-word vocabulary the coverage strip uses. "The model found nothing" and
 *   "nobody looked" were the same silence before, and only one of them is a
 *   finding about a person.
 *
 * Coverage sits at the FOOT of this page while the TUI puts it under the
 * headline. That is deliberate rather than drift: the header already carries a
 * SOURCES eyebrow and the nav links straight to it, so the top of the document
 * is spoken for — whereas the TUI has neither, and a compact strip under the
 * headline is its only chance to frame what follows.
 */

import {
  Avatar,
  NoticeBlock,
  Prose,
  proseBullets,
  StatBar,
  StatGrid,
  StatTile,
} from '../../design/primitives';
import { EVIDENCE_SOURCE_LABELS } from '../../types/enums';
import type { EditMap, PerfCoverage, PerfEvidenceGroup, PerfSection, PerfStat } from '../boot';
import { EditableSlot } from '../editing/Editable';
import { Field } from '../editing/Field';
import { CoverageDots } from './Coverage';
import { EvidenceList } from './Evidence';
import styles from './reports.module.css';

/** Long enough that it is packed prose, not a bullet someone wrote. */
const SPLIT_OVER = 160;

/** Headline tiles. More than this and they stop being headlines. */
const TILE_COUNT = 4;

/** How an empty section names its own emptiness, per coverage state. */
const EMPTY_PHRASE: Record<string, string> = {
  covered: 'None found in this period.',
  partial: 'Partly scanned.',
  failed: 'Could not be read.',
  not_configured: 'Not assessed.',
};

function sourceLabel(source: string): string {
  return EVIDENCE_SOURCE_LABELS[source as keyof typeof EVIDENCE_SOURCE_LABELS] ?? source;
}

/** A number formatted as what it is. An unknown unit degrades to the bare number. */
function statValue(stat: PerfStat): string {
  if (stat.of) return `${stat.value} / ${stat.of}`;
  if (stat.unit === '%') return `${stat.value}%`;
  if (stat.unit === 'pts') return `${stat.value} pts`;
  if (stat.unit === 'd') return `${stat.value}d`;
  return String(stat.value);
}

function Stats({ stats }: { stats: PerfStat[] }) {
  if (!stats.length) return null;
  return (
    <section id="numbers">
      <h2 className={styles['h2']}>By the numbers</h2>
      <StatGrid>
        {stats.slice(0, TILE_COUNT).map((stat) => (
          <StatTile
            key={stat.id}
            value={statValue(stat)}
            label={stat.label}
            {...(stat.hint ? { hint: stat.hint } : {})}
          >
            {/* The bar sits inside the tile it describes: a ratio's bar under
                the whole grid reads as page progress, not as "12 of 14". */}
            {stat.of ? (
              <StatBar pct={(stat.value / stat.of) * 100} label={`${stat.label}: ${statValue(stat)}`} />
            ) : null}
          </StatTile>
        ))}
      </StatGrid>
      {stats.length > TILE_COUNT ? (
        <ul className={styles['details']}>
          {stats.slice(TILE_COUNT).map((stat) => (
            <li key={stat.id}>
              {stat.label} — {statValue(stat)}
              {stat.hint ? ` (${stat.hint})` : ''}{' '}
              <span className={styles['dim']}>{sourceLabel(stat.source)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Coverage({ coverage }: { coverage: PerfCoverage[] }) {
  if (!coverage.length) return null;
  // Only a gap spends a line on its reason: "covered" explains itself, and a
  // sentence beside every source would bury the ones that need reading.
  const gaps = coverage.filter((row) => row.state !== 'covered' && row.detail);
  return (
    <section id="coverage">
      <h2 className={styles['h2']}>What this was built from</h2>
      <p className={styles['chipRow']}>
        <CoverageDots
          items={coverage.map((row) => ({
            label: sourceLabel(row.source),
            status: row.state,
            ...(row.detail ? { detail: row.detail } : {}),
          }))}
        />
      </p>
      {gaps.length ? (
        <ul className={styles['details']}>
          {gaps.map((row) => (
            <li key={row.source}>
              {sourceLabel(row.source)} — {row.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Evidence({ groups }: { groups: PerfEvidenceGroup[] }) {
  const present = groups.filter((group) => group.items.length);
  if (!present.length) return null;
  return (
    <section id="evidence">
      <h2 className={styles['h2']}>Evidence</h2>
      {present.map((group) => (
        <div key={group.source}>
          <h3 className={styles['categoryHead']}>{group.label || sourceLabel(group.source)}</h3>
          <EvidenceList items={group.items} id={`ev-${group.source}`} />
          {group.note ? <p className={styles['dim']}>{group.note}</p> : null}
        </div>
      ))}
    </section>
  );
}

function Section({ section }: { section: PerfSection }) {
  if (!section.items.length) {
    // The heading still renders. A section that simply vanished was
    // indistinguishable from one that was never asked for.
    if (!section.state) return null;
    return (
      <section id={section.id}>
        <h2 className={styles['h2']}>{section.title}</h2>
        <p className={styles['empty']}>
          {EMPTY_PHRASE[section.state] ?? 'Not reported.'}
          {section.reason ? ` ${section.reason}` : ''}
        </p>
      </section>
    );
  }
  return (
    <section id={section.id}>
      <h2 className={styles['h2']}>{section.title}</h2>
      <ul className={styles['bullets']}>
        {section.items.flatMap((item, i) =>
          // A long item is one sentence carrying three facts; splitting it is
          // the difference between a scannable list and a wall. A short one is
          // already a bullet, and splitting it would fragment a written thought.
          // Flattened into this list rather than nesting a second <ul>, which
          // is invalid outside an <li> and indents for no reason.
          (item.length > SPLIT_OVER ? proseBullets(item) : [item]).map((fragment, j) => (
            <li key={`${i}-${j}`}>{fragment}</li>
          ))
        )}
      </ul>
    </section>
  );
}

export function Performance({
  engineer,
  period,
  lead,
  edit,
  sections,
  stats = [],
  coverage = [],
  evidence = [],
  footnote,
  warnings,
}: {
  engineer: string;
  period?: { start: string; end: string };
  lead?: { title: string; text: string; field?: string };
  edit?: EditMap;
  sections: PerfSection[];
  stats?: PerfStat[];
  coverage?: PerfCoverage[];
  evidence?: PerfEvidenceGroup[];
  footnote?: string;
  warnings: string[];
}) {
  return (
    <>
      {engineer ? (
        <p className={styles['identity']}>
          <Avatar name={engineer} />
          <strong>{engineer}</strong>
          {period ? (
            <span className={styles['dim']}>
              {period.start} – {period.end}
            </span>
          ) : null}
        </p>
      ) : null}

      <EditableSlot anchor="" label="this review" />
      {lead ? (
        <section id="summary">
          <h2 className={styles['h2']}>{lead.title}</h2>
          <Field edit={edit} field={lead.field ?? ''} label={lead.title.toLowerCase()}>
            <Prose text={lead.text} />
          </Field>
        </section>
      ) : null}

      <Stats stats={stats} />

      {sections.map((section) => (
        <Section key={section.id} section={section} />
      ))}

      <Evidence groups={evidence} />
      <Coverage coverage={coverage} />

      {footnote ? <p className={styles['footnote']}>{footnote}</p> : null}
      <NoticeBlock title="Notices" items={warnings} />
    </>
  );
}
