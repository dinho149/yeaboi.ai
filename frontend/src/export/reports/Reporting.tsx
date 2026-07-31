/**
 * The delivery report: what shipped in a period, for someone who was not there.
 *
 * This is the export with the widest audience — it goes to stakeholders, not to
 * the team — which is why it is the one that leads with a sentence and a row of
 * numbers rather than with a table. The table is at the bottom, for the reader
 * who wants to check a specific ticket.
 *
 * **The breakdown is a bar here and a PNG in the Markdown twin, deliberately.**
 * `export.py` still renders a matplotlib chart for the Markdown/Notion/
 * Confluence path, because those destinations cannot draw one. This page can:
 * the segment bar is `var(--…)` all the way down, so it recolours with the theme
 * and prints, which a raster image does neither of.
 *
 * The emoji are the host's own choice of section decoration, carried per slot.
 * They are rendered as text next to the heading rather than baked into it, so a
 * report with no theme chosen is not left with a stray leading space.
 */

import {
  Avatar,
  Chip,
  countedSegments,
  DataTable,
  Legend,
  NoticeBlock,
  Prose,
  proseBullets,
  SegmentBar,
  StatGrid,
  StatTile,
} from '../../design/primitives';
import type { DeliveredItem, ReportTheme, Trend } from '../boot';
import styles from './reports.module.css';
import { TrendCard } from './Trend';

/** Long enough that it is packed prose, not a bullet someone wrote. Matches `Performance`. */
const SPLIT_OVER = 160;

function sectionId(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function Heading({ emoji, children }: { emoji?: string | undefined; children: string }) {
  return (
    <h2 className={styles['h2']}>
      {emoji ? (
        <span className={styles['headingEmoji']} aria-hidden="true">
          {emoji}
        </span>
      ) : null}
      {children}
    </h2>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className={styles['bullets']}>
      {items.flatMap((item, i) =>
        (item.length > SPLIT_OVER ? proseBullets(item) : [item]).map((fragment, j) => (
          <li key={`${i}-${j}`}>{fragment}</li>
        ))
      )}
    </ul>
  );
}

export function Reporting({
  headline,
  metrics,
  summary,
  themes,
  highlights,
  items,
  breakdown,
  emoji,
  trend,
  warnings,
}: {
  headline?: string;
  metrics: Array<[string, string]>;
  summary?: string;
  themes: ReportTheme[];
  highlights: string[];
  items: DeliveredItem[];
  breakdown: Array<[string, number]>;
  emoji: Record<string, string>;
  trend: Trend | null;
  warnings: string[];
}) {
  const { segments, legend } = countedSegments(breakdown);

  return (
    <>
      {headline ? <blockquote className={styles['lede']}>{headline}</blockquote> : null}

      {metrics.length ? (
        <section id="numbers">
          <Heading emoji={emoji['metrics']}>By the numbers</Heading>
          <StatGrid>
            {metrics.map(([label, value]) => (
              <StatTile key={label} value={value} label={label} />
            ))}
          </StatGrid>
        </section>
      ) : null}

      <TrendCard trend={trend} />

      {summary ? (
        <section id="summary">
          <Heading emoji={emoji['summary']}>Executive summary</Heading>
          {/* `Prose`, not a paragraph with the newlines swapped for `<br>`: the
              summary's line breaks are the model's own paragraphing, and
              `white-space: pre-wrap` keeps them without parsing anything. */}
          <Prose text={summary} />
        </section>
      ) : null}

      {themes.map((theme) => (
        <section key={theme.title} id={sectionId(theme.title)}>
          <Heading emoji={emoji['themes']}>{theme.title}</Heading>
          <Bullets items={theme.outcomes} />
        </section>
      ))}

      {highlights.length ? (
        <section id="highlights">
          <Heading emoji={emoji['highlights']}>Highlights</Heading>
          <Bullets items={highlights} />
        </section>
      ) : null}

      {items.length ? (
        <section id="delivered">
          {/* No slot for this one: the emoji theme decorates the sections the
              model wrote, and the item table is the tracker's own record. */}
          <Heading>Delivered items</Heading>
          {segments.length ? (
            <div className={styles['split']}>
              <SegmentBar segments={segments} label="Delivered items breakdown" />
              <Legend items={legend} />
            </div>
          ) : null}
          <DataTable
            rows={items}
            rowKey={(item, index) => `${index}-${item.key}`}
            columns={[
              { key: 'Key', cell: (item) => <Chip>{item.key}</Chip> },
              { key: 'Title', cell: (item) => item.title },
              { key: 'Status', cell: (item) => item.status },
              {
                key: 'Delivered by',
                cell: (item) =>
                  item.assignee ? (
                    <span className={styles['person']}>
                      <Avatar name={item.assignee} size={20} />
                      {item.assignee}
                    </span>
                  ) : (
                    '—'
                  ),
              },
            ]}
          />
        </section>
      ) : null}

      <NoticeBlock title="Notices" items={warnings} />
    </>
  );
}
