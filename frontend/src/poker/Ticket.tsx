/**
 * The ticket under discussion — and the one you wandered off to read.
 *
 * ## Peek
 *
 * Anyone can click a rail row to read a different ticket. That is local and
 * read-only: nothing is posted, nobody else's view moves, and the round carries
 * on. It exists because the question "wait, is this the one that depends on the
 * migration?" comes up constantly, and the old answer was to ask the host to
 * navigate the whole room away from the ticket they were mid-vote on.
 *
 * The trap peek creates is voting into the wrong ticket, so the deck closes
 * while a preview is up and says why. That guard lives in App, because it is the
 * deck's behaviour, not the ticket panel's.
 *
 * ## Collapsibles
 *
 * Descriptions from a real tracker run to several screens. Both the description
 * and the acceptance criteria clip at a fixed height with a toggle, and the open
 * state resets when the ticket changes — carrying it across would open the next
 * ticket's body scrolled to a paragraph nobody asked for.
 */

import { useEffect, useRef, useState } from 'react';

import { Chip, Eyebrow, Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import { safeUrl } from '../runtime/url';
import type { PokerPhase, PokerTicket, TicketView } from '../types/board';
import { fmtPoints } from './points';
import { Button } from '../shared';
import styles from './poker.module.css';

/** Live tickets and peeked ones share every display field. */
type Displayable = PokerTicket | TicketView;

interface BodyProps {
  ticket: Displayable;
  tag: React.ReactNode;
}

function Collapsible({ label, text }: { label?: string; text: string }) {
  const [open, setOpen] = useState(false);
  const [clips, setClips] = useState(false);
  const body = useRef<HTMLDivElement | null>(null);

  // Reset when the body changes — i.e. when the ticket does. Keyed on the text
  // rather than the key so a host edit that shortens a description also
  // re-collapses it, which is the same "this is new content" situation.
  useEffect(() => setOpen(false), [text]);

  // Does the body overrun its clamp? Not measured while open, where the clamp
  // is off; `clips` keeps its collapsed answer.
  useEffect(() => {
    const el = body.current;
    if (!el || open) return;
    const measure = (): void => setClips(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver !== 'function') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, open]);

  return (
    <>
      {label ? <Eyebrow className={styles['bodyLabel']}>{label}</Eyebrow> : null}
      <div
        ref={body}
        className={cx(styles['desc'], !open && styles['descClipped'], clips && !open && styles['descFade'])}
      >
        {text}
      </div>
      {clips ? (
        <button type="button" className={styles['descToggle']} aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name={open ? 'chevron-up' : 'chevron-down'} /> {open ? 'Show less' : 'Show more'}
        </button>
      ) : null}
    </>
  );
}

function TicketBody({ ticket, tag }: BodyProps) {
  const href = safeUrl(ticket.url);
  return (
    <>
      <div className={styles['tkrow']}>
        <span className={styles['key']}>
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {ticket.key} <Icon name="external-link" size={12} />
            </a>
          ) : (
            ticket.key
          )}
        </span>
        {tag}
      </div>

      <div className={styles['tkHead']}>
        <h1 className={styles['tkSummary']}>{ticket.summary}</h1>

        <div className={styles['chips']}>
        {ticket.type ? <Chip>{ticket.type}</Chip> : null}
        {ticket.state ? (
          <Chip>
            status <b>{ticket.state}</b>
          </Chip>
        ) : null}
        {ticket.assignee ? (
          <Chip>
            assignee <b>{ticket.assignee}</b>
          </Chip>
        ) : null}
        <Chip>
          points <b>{fmtPoints(ticket.story_points)}</b>
        </Chip>
          {ticket.estimated ? (
            <Chip tone="ok">
              <Icon name="check" size={12} /> estimated <b>{fmtPoints(ticket.final_points)}</b>
            </Chip>
          ) : null}
        </div>
      </div>

      {ticket.description_text ? (
        <Collapsible text={ticket.description_text} />
      ) : (
        <p className={styles['descEmpty']}>No description.</p>
      )}

      {/* Acceptance criteria get their own labelled section, and are omitted
          rather than empty-stated — most trackers simply do not have them. */}
      {ticket.acceptance_text ? <Collapsible label="Acceptance criteria" text={ticket.acceptance_text} /> : null}
    </>
  );
}

export interface TicketPanelProps {
  /** The live ticket, or null when the batch is empty. */
  ticket: PokerTicket | null;
  phase: PokerPhase;
  index: number;
  count: number;
  /** The peeked ticket's body, once fetched. Null while loading. */
  peek: TicketView | null;
  /** Index being peeked, or null when showing live. */
  peekIndex: number | null;
  /** Key of the live ticket, for the "the team is voting on X" line. */
  liveKey: string;
  isHost: boolean;
  onBackToLive(): void;
  /** Host only: move the whole room to the ticket being previewed. */
  onGotoPeek(): void;
}

export function TicketPanel({
  ticket,
  phase,
  index,
  count,
  peek,
  peekIndex,
  liveKey,
  isHost,
  onBackToLive,
  onGotoPeek,
}: TicketPanelProps) {
  if (peekIndex !== null) {
    return (
      <section className={cx(styles['ticket'], styles['ticketPeek'])} aria-label="Ticket preview">
        <div className={styles['peekBanner']}>
          <span>
            <Icon name="eye" /> Previewing <b>{peek?.key || `ticket ${peekIndex + 1}`}</b> — the team is
            voting on <b>{liveKey || `ticket ${index + 1}`}</b>
          </span>
          <span className={styles['peekActions']}>
            <Button size="s" onClick={onBackToLive}>
              Back to live
            </Button>
            {/* Jumping the room resets the round, so it is an explicit button
                inside the banner rather than a side effect of a rail click. */}
            {isHost && phase !== 'duel' ? (
              <Button size="s" className={styles['pkbtnGo']} onClick={onGotoPeek}>
                Vote on this ticket
              </Button>
            ) : null}
          </span>
        </div>
        {peek ? (
          <TicketBody ticket={peek} tag={<span className={cx(styles['phaseTag'], styles['phaseTagPeek'])}>
              <Icon name="eye" size={12} /> preview
            </span>} />
        ) : (
          <p className={styles['descEmpty']}>Loading ticket…</p>
        )}
      </section>
    );
  }

  if (!ticket) {
    return (
      <section className={styles['ticket']} aria-label="Ticket">
        <p className={styles['vempty']}>No tickets loaded.</p>
      </section>
    );
  }

  const tag =
    phase === 'revealed' ? (
      <span className={cx(styles['phaseTag'], styles['phaseTagOn'])}>votes revealed</span>
    ) : phase === 'duel' ? (
      <span className={cx(styles['phaseTag'], styles['phaseTagOn'])}>
        <Icon name="swords" size={12} /> the floor is open
      </span>
    ) : (
      <span className={styles['phaseTag']}>
        voting {index + 1}/{count}
      </span>
    );

  return (
    <section className={styles['ticket']} aria-label="Ticket">
      <TicketBody ticket={ticket} tag={tag} />
    </section>
  );
}
