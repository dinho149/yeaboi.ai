/**
 * The team profile — the analysis report, and the one built from blocks.
 *
 * See the note on `Block` in `boot.ts` for why this report is described with a
 * block vocabulary rather than twenty-odd named interfaces: its sections are
 * *generated*, and which ones exist depends on which analyses were enabled and
 * which sources answered. There is nothing stable to name.
 *
 * What that buys, beyond the ~1400 lines of f-string HTML it replaced: a new
 * sub-analysis is a new `blocks.append(...)` in Python and nothing here, and
 * every section of the report is guaranteed to look like every other one —
 * which the string version could not promise, since each section built its own
 * markup and drifted from the others one inline `style=` at a time.
 */

import {
  Avatar,
  Card,
  Chip,
  countedSegments,
  DataTable,
  Eyebrow,
  Legend,
  NoticeBlock,
  Prose,
  RichText,
  SegmentBar,
  StatBar,
} from '../../design/primitives';
import { TONES, toneMix, toneVar, type Tone } from '../../design/tone';
import type { Block, Cell, ProfileSection } from '../boot';
import styles from './reports.module.css';
import { TrendCard } from './Trend';

/**
 * Gate a payload tone against the closed vocabulary.
 *
 * `Tone` is a compile-time type, and this payload is JSON — so the one thing
 * the type cannot promise is that what actually arrived is a member. It reaches
 * `toneVar`, which builds a `var(--…)` for a `style` attribute, so an unchecked
 * string is the one field on this page that could carry CSS. Every producer is
 * a Python constant today; this makes that a fact about the code rather than a
 * fact about the current callers.
 */
function asTone(tone: string | undefined): Tone | undefined {
  return (TONES as readonly string[]).includes(tone ?? '') ? (tone as Tone) : undefined;
}

/**
 * `context` is what the cell is a value *of* — the key/value row's label, or a
 * table column's header. It exists for the bar: a `role="img"` labelled only
 * "76%" makes a screen reader say the number twice, since the number is already
 * the adjacent text. The rest of the primitives label bars the same way
 * ("capacity 29 of 40 points"), and a bar with no context is the one thing here
 * that would read as noise.
 */
function CellView({ cell, context = '' }: { cell: Cell; context?: string }) {
  if (typeof cell === 'string') return <>{cell}</>;

  const tone = asTone(cell.tone);
  const text = cell.href ? (
    <Chip {...(tone ? { tone } : {})} href={cell.href}>
      {cell.t}
    </Chip>
  ) : (
    <span style={tone ? { color: toneVar(tone) } : undefined}>{cell.t}</span>
  );

  return (
    <span className={styles['cell']}>
      {cell.person ? <PersonName name={cell.t} /> : text}
      {/* A proportion draws its own bar *beside* the number, never instead of
          it. The bar is the comparison; the number is the fact. */}
      {cell.pct === undefined ? null : (
        <StatBar
          pct={cell.pct}
          {...(tone ? { tone } : {})}
          label={context ? `${context}: ${cell.t}` : cell.t}
          className={styles['cellBar']}
        />
      )}
      {cell.note ? <span className={styles['cellNote']}>{cell.note}</span> : null}
    </span>
  );
}

/** A name with its deterministic avatar disc — the table's version of a byline. */
function PersonName({ name }: { name: string }) {
  return (
    <span className={styles['cellPerson']}>
      <Avatar name={name} size={20} />
      {name}
    </span>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case 'kv':
      return (
        <div className={styles['kvWrap']}>
          {block.title ? <h3 className={styles['h3']}>{block.title}</h3> : null}
          <dl className={styles['kv']}>
            {block.rows.map(([label, value], index) => (
              <div key={`${index}-${label}`}>
                <dt>{label}</dt>
                <dd>
                  <CellView cell={value} context={label} />
                </dd>
              </div>
            ))}
          </dl>
        </div>
      );

    case 'table':
      return (
        <div className={styles['kvWrap']}>
          {block.title ? <h3 className={styles['h3']}>{block.title}</h3> : null}
          <DataTable
            rows={block.rows}
            rowKey={(_row, index) => `${index}`}
            columns={block.headers.map((header, column) => ({
              key: header || `col-${column}`,
              header,
              numeric: block.numeric?.includes(column) ?? false,
              cell: (row: Cell[]) => <CellView cell={row[column] ?? ''} context={header} />,
            }))}
          />
        </div>
      );

    case 'cards':
      return (
        <div className={styles['kvWrap']}>
          {block.title ? <h3 className={styles['h3']}>{block.title}</h3> : null}
          <div className={styles['profileCards']}>
            {block.cards.map((card) => (
              <Card key={card.title} title={card.title}>
                <ul className={styles['bullets']}>
                  {card.items.map((runs, index) => (
                    <li key={index}>
                      <RichText runs={runs} />
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        </div>
      );

    case 'bullets': {
      const List = block.ordered ? 'ol' : 'ul';
      return (
        <div className={styles['kvWrap']}>
          {block.title ? <h3 className={styles['h3']}>{block.title}</h3> : null}
          <List className={styles['bullets']}>
            {block.items.map((runs, index) => (
              <li key={index}>
                <RichText runs={runs} />
              </li>
            ))}
          </List>
        </div>
      );
    }

    case 'prose':
      return <Prose text={block.text} className={styles['profileProse']} />;

    case 'note':
      // Muted and italic: a caveat about what the numbers cannot show should
      // read as a qualification of the section, not as one of its findings.
      return <p className={styles['note']}>{block.text}</p>;

    case 'callout': {
      const tone = asTone(block.tone) ?? 'warn';
      return (
        <div
          className={styles['callout']}
          style={{ borderLeftColor: toneVar(tone), background: toneMix(tone, 6) }}
        >
          <strong style={{ color: toneVar(tone) }}>{block.title}</strong>
          {block.text ? <p>{block.text}</p> : null}
          {block.items?.length ? (
            <ul className={styles['bullets']}>
              {block.items.map((runs, index) => (
                <li key={index}>
                  <RichText runs={runs} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      );
    }

    case 'bar': {
      const { segments, legend } = countedSegments(block.counts);
      if (!segments.length) return null;
      return (
        <div className={styles['split']}>
          <Eyebrow>{block.label}</Eyebrow>
          <SegmentBar segments={segments} label={block.label} />
          <Legend items={legend} />
        </div>
      );
    }

    case 'trend':
      return <TrendCard trend={block.trend} />;

    case 'image':
      return <img className={styles['screenshot']} src={block.src} alt={block.alt} />;

    default: {
      // Same guard as the report switch: a block kind Python starts emitting
      // that nothing draws is a silently empty section in a file nobody can
      // debug after the fact.
      const unreachable: never = block;
      throw new Error(`profile: no renderer for ${JSON.stringify(unreachable)}`);
    }
  }
}

export function Profile({ sections, coverage }: { sections: ProfileSection[]; coverage: string[] }) {
  if (!sections.length) {
    return <p className={styles['empty']}>No analysis to export yet.</p>;
  }

  return (
    <>
      {/* Before the numbers, not after: a profile assembled from two of five
          sources is a different document from a complete one. */}
      <NoticeBlock title="Coverage" items={coverage} />

      {sections.map((section) => (
        <section key={section.id} id={section.id}>
          <h2 className={styles['h2']}>{section.title}</h2>
          <div className={styles['blocks']}>
            {section.blocks.map((block, index) => (
              <BlockView key={index} block={block} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
