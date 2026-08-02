/**
 * The retro export: the board, after everyone has gone home.
 *
 * The columns carry the same four tones as the live board — `GRID_TONE`, the
 * single typed map, imported rather than re-stated. The copy this replaces had
 * already drifted: `retro/export.py` held its own `_GRID_VARS` dict, and it gave
 * Demos `--info` where the board gives it `accent2`. Blue in the file, violet in
 * the meeting, for the same column on the same afternoon.
 *
 * Empty columns become footnotes rather than empty cards. A board where nobody
 * wrote a demo is the normal case, and four equal cards, two of them blank, give
 * the same visual weight to what the team said and to what it did not.
 */

import { Avatar, Chip, Legend, SegmentBar } from '../../design/primitives';
import { toneVar } from '../../design/tone';
import { GRID_TONE } from '../../retro/gridTone';
import { CARRIED_STATUS_LABELS, RETRO_GRID_LABELS } from '../../types/enums';
import type { CarriedItem, RetroCard, RetroColumn, Trend } from '../boot';
import { Field } from '../editing/Field';
import styles from './reports.module.css';
import { TrendCard } from './Trend';

/** `👍 3 🔥 1` — the counts, never the zeroes. */
function reactionText(card: RetroCard): string {
  return card.reactions
    .filter(([, count]) => count > 0)
    .map(([emoji, count]) => `${emoji} ${count}`)
    .join('  ');
}

function Attribution({ card }: { card: RetroCard }) {
  // "(AI)" rather than an avatar for the facilitator's own cards: an initials
  // circle would make the model look like another person in the room.
  if (card.ai) return <em className={styles['byline']}>(AI)</em>;
  if (!card.author) return null;
  return (
    <em className={styles['byline']}>
      — <Avatar name={card.author} size={18} />
      {card.author}
    </em>
  );
}

/**
 * A plain element rather than the `Card` primitive, matching the live board's
 * own `Column`: the tone rides in as a custom property the stylesheet reads, and
 * a primitive that took an arbitrary `style` for one caller would be a worse
 * primitive.
 */
function Column({ column }: { column: RetroColumn }) {
  const label = RETRO_GRID_LABELS[column.grid];
  return (
    <div className={styles['column']} style={{ '--col-tone': toneVar(GRID_TONE[column.grid]) } as never}>
      <h3 className={styles['columnTitle']}>
        {label}
        <span className={styles['count']}>{column.cards.length}</span>
      </h3>
      <ul className={styles['bullets']}>
        {column.cards.map((card, index) => {
          const reactions = reactionText(card);
          return (
            <li key={`${index}-${card.text.slice(0, 24)}`}>
              <Field edit={card.edit} field="text" label="this card" inline>
                <>{card.text}</>
              </Field>{' '}
              <Attribution card={card} />
              {reactions ? <Chip className={styles['reactions']}>{reactions}</Chip> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function Retro({
  columns,
  participants,
  carried,
  trend,
}: {
  columns: RetroColumn[];
  participants: string[];
  carried: CarriedItem[];
  trend: Trend | null;
}) {
  const filled = columns.filter((column) => column.cards.length);
  const empty = columns.filter((column) => !column.cards.length);

  // Board order, not count order — so `countedSegments`, which sorts descending,
  // is deliberately not used here. Descending is right for an anonymous
  // breakdown and wrong for one whose categories the reader already knows the
  // arrangement of; a bar whose stripes move between two retros is unreadable.
  const bars = filled.map((column) => ({
    label: RETRO_GRID_LABELS[column.grid],
    count: column.cards.length,
    tone: GRID_TONE[column.grid],
  }));

  return (
    <>
      {participants.length ? (
        <p className={styles['people']}>
          <strong>Participants:</strong>
          {participants.map((name) => (
            <span key={name} className={styles['person']}>
              <Avatar name={name} size={22} />
              {name}
            </span>
          ))}
        </p>
      ) : null}

      {bars.length ? (
        <div className={styles['split']}>
          <SegmentBar
            segments={bars.map((bar) => ({ value: bar.count, tone: bar.tone, label: bar.label }))}
            label="Cards per column"
          />
          <Legend items={bars} />
        </div>
      ) : null}

      <TrendCard trend={trend} />

      {filled.length ? (
        <div className={styles['columns']}>
          {filled.map((column) => (
            <Column key={column.grid} column={column} />
          ))}
        </div>
      ) : null}

      {empty.map((column) => (
        <p key={column.grid} className={styles['footnote']}>
          {RETRO_GRID_LABELS[column.grid]} — no cards.
        </p>
      ))}

      {carried.length ? (
        <section id="carried">
          <h2 className={styles['h2']}>Last sprint&rsquo;s action items — progress</h2>
          <ul className={styles['bullets']}>
            {carried.map((item, index) => (
              <li key={`${index}-${item.text.slice(0, 24)}`}>
                <strong className={styles['status']}>[{CARRIED_STATUS_LABELS[item.status]}]</strong> {item.text}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
