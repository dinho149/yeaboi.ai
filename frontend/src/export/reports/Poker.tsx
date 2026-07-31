/**
 * The planning-poker export: what the room estimated, and what it argued about.
 *
 * The ticket table is the artifact people actually come back for, so it stays a
 * table — a grid of cards would be prettier and worse at "what did we say
 * PROJ-14 was". Everything above it exists to make the table legible at a
 * glance: how much of the scope got estimated, whether that is normal for this
 * team, and who was in the room.
 *
 * **Skipped is an outcome, not a gap.** A ticket the room declined to estimate
 * is a decision worth reading back, so it keeps its row and says so, rather than
 * showing an empty cell that looks like the export lost something.
 */

import {
  Avatar,
  Card,
  Chip,
  DataTable,
  Legend,
  ProseBullets,
  Prose,
  SegmentBar,
  StatGrid,
  StatTile,
} from '../../design/primitives';
import type { PokerTicket, PokerVote, Trend } from '../boot';
import styles from './reports.module.css';
import { TrendCard } from './Trend';

/**
 * Points as text; absent is an em dash.
 *
 * No `.0` stripping, unlike the Python `_pts` it replaces — Python's `5.0` is a
 * float and prints as one, JavaScript's is the same number as `5`.
 */
function points(value: number | null): string {
  return value === null ? '—' : String(value);
}

function Votes({ votes }: { votes: PokerVote[] }) {
  if (!votes.length) return <>—</>;
  return (
    <span className={styles['votes']}>
      {votes.map((vote) => (
        // Initials, not the full name: this is a table column read down the
        // page, and two names per row would push the summary off a phone. The
        // label puts the name back for anyone who is not reading it visually —
        // the avatar is `aria-hidden`, so without this the votes column is
        // announced as a bare run of numbers with nobody attached to them.
        <span
          key={`${vote.voter}-${vote.value}`}
          className={styles['vote']}
          aria-label={`${vote.voter} voted ${vote.value}`}
        >
          <Avatar name={vote.voter} size={20} />
          {vote.value}
        </span>
      ))}
    </span>
  );
}

function People({ names }: { names: string[] }) {
  if (!names.length) return null;
  return (
    <p className={styles['people']}>
      <strong>Participants:</strong>
      {names.map((name) => (
        <span key={name} className={styles['person']}>
          <Avatar name={name} size={22} />
          {name}
        </span>
      ))}
    </p>
  );
}

export function Poker({
  tickets,
  participants,
  trend,
}: {
  tickets: PokerTicket[];
  participants: string[];
  trend: Trend | null;
}) {
  const estimated = tickets.filter((ticket) => ticket.estimated).length;
  const skipped = tickets.length - estimated;
  const notes = tickets.filter((ticket) => ticket.aiNote);
  const duels = tickets.filter((ticket) => ticket.duel);

  return (
    <>
      <section id="overview">
        <h2 className={styles['h2']}>Overview</h2>
        <StatGrid>
          <StatTile value={tickets.length} label="Tickets" />
          <StatTile value={estimated} label="Estimated" />
          <StatTile value={participants.length} label="Participants" />
        </StatGrid>
        {tickets.length ? (
          <div className={styles['split']}>
            <SegmentBar
              segments={[
                { value: estimated, tone: 'ok', label: 'Estimated' },
                { value: skipped, tone: 'muted', label: 'Skipped' },
              ]}
              label={`${estimated} of ${tickets.length} tickets estimated`}
            />
            <Legend
              items={[
                { label: 'Estimated', count: estimated, tone: 'ok' },
                { label: 'Skipped', count: skipped, tone: 'muted' },
              ]}
            />
          </div>
        ) : null}
        <TrendCard trend={trend} />
        <People names={participants} />
      </section>

      <section id="tickets">
        <h2 className={styles['h2']}>Tickets</h2>
        <DataTable
          rows={tickets}
          rowKey={(ticket, index) => `${index}-${ticket.key}`}
          empty={<p className={styles['empty']}>No tickets were brought to the session.</p>}
          columns={[
            {
              key: 'Ticket',
              // The chip is the link; an unsafe `url` degrades to a plain chip
              // rather than disappearing, because the key is the identity.
              cell: (ticket) => <Chip {...(ticket.url ? { href: ticket.url } : {})}>{ticket.key}</Chip>,
            },
            { key: 'Summary', cell: (ticket) => ticket.summary },
            { key: 'Before', numeric: true, cell: (ticket) => points(ticket.before) },
            {
              key: 'Final',
              numeric: true,
              cell: (ticket) =>
                ticket.estimated ? points(ticket.final) : <em className={styles['skipped']}>skipped</em>,
            },
            { key: 'Votes', cell: (ticket) => <Votes votes={ticket.votes} /> },
          ]}
        />
      </section>

      {notes.length ? (
        <section id="ai">
          <h2 className={styles['h2']}>AI perspectives</h2>
          <div className={styles['notes']}>
            {notes.map((ticket) => (
              <Card key={ticket.key} title={ticket.key}>
                <ProseBullets text={ticket.aiNote as string} />
              </Card>
            ))}
          </div>
        </section>
      ) : null}

      {duels.length ? (
        <section id="duels">
          <h2 className={styles['h2']}>Duels</h2>
          {duels.map((ticket) => {
            const duel = ticket.duel as NonNullable<PokerTicket['duel']>;
            return (
              <Card
                key={ticket.key}
                title={ticket.key}
                actions={
                  // `low`/`high` are the priority tones, borrowed for the two
                  // ends of an estimate spread. The reuse is about magnitude,
                  // not urgency — a duel is by definition the lowest vote
                  // against the highest, and cool-to-warm says which is which
                  // without a label. Nothing here has a priority to confuse it
                  // with.
                  <div className={styles['chips']}>
                    <Chip tone="low">{duel.low}</Chip>
                    <Chip tone="high">{duel.high}</Chip>
                  </div>
                }
              >
                {/* Transcribed speech, so `Prose` — its line breaks are the turn
                    structure, and they are the only markup it will ever have. */}
                <Prose text={duel.transcript} className={styles['transcript']} />
              </Card>
            );
          })}
        </section>
      ) : null}
    </>
  );
}
