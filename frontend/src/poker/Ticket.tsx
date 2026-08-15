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

import { Eyebrow, Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import { safeUrl } from '../runtime/url';
import type { PokerPhase, PokerTicket, TicketView } from '../types/board';
import type { TicketEdit } from './actions';
import { fmtPoints } from './points';
import { Button } from '../shared';
import styles from './poker.module.css';

/** Live tickets and peeked ones share every display field. */
type Displayable = PokerTicket | TicketView;

interface BodyProps {
  ticket: Displayable;
  tag: React.ReactNode;
  /** Host-only, and absent on a preview: editing applies to the live ticket. */
  onEdit?: (() => void) | undefined;
}

/** The values seen across the board's own tickets — see `ticketOptions`. */
export interface TicketOptions {
  types: string[];
  states: string[];
  assignees: string[];
}

/**
 * The choices a picker offers.
 *
 * Read off the tickets already loaded rather than from the tracker: a project's
 * real schema is per-instance and is not on the wire, and this is both true of
 * the board in front of you and free. It is therefore the observed set, not the
 * whole vocabulary — so each picker also accepts a typed value.
 */
export function ticketOptions(tickets: readonly { type: string; state: string; assignee: string }[]): TicketOptions {
  const gather = (pick: (t: { type: string; state: string; assignee: string }) => string): string[] =>
    [...new Set(tickets.map(pick).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  return {
    types: gather((t) => t.type),
    states: gather((t) => t.state),
    assignees: gather((t) => t.assignee),
  };
}

/** A property whose value is picked from what the board already uses. */
function PropPicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange(next: string): void;
}) {
  const listId = `poker-opt-${label.toLowerCase()}`;
  return (
    <div className={styles['prop']}>
      <dt className={styles['propLabel']}>{label}</dt>
      <dd className={styles['propValue']}>
        <input
          className={styles['editPick']}
          aria-label={label}
          list={listId}
          value={value}
          onInput={(event) => onChange((event.target as HTMLInputElement).value)}
        />
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      </dd>
    </div>
  );
}

/**
 * The ticket, editable in place.
 *
 * The fields are the ticket — the same type at the same size in the same
 * position — so the panel does not become a form on top of what it is editing.
 * Nothing changed is a cancel that went through Save: an empty edit still bumps
 * the ticket's history on the tracker.
 */
function TicketForm({
  ticket,
  options,
  onSave,
  onCancel,
}: {
  ticket: PokerTicket;
  options: TicketOptions;
  onSave(edit: TicketEdit): void;
  onCancel(): void;
}) {
  const [summary, setSummary] = useState(ticket.summary);
  const [description, setDescription] = useState(ticket.description_text);
  const [points, setPoints] = useState(ticket.story_points === null ? '' : fmtPoints(ticket.story_points));
  const [type, setType] = useState(ticket.type);
  const [state, setState] = useState(ticket.state);
  const [assignee, setAssignee] = useState(ticket.assignee);
  const [acceptance, setAcceptance] = useState(ticket.acceptance_text);
  const body = useRef<HTMLTextAreaElement | null>(null);
  const criteria = useRef<HTMLTextAreaElement | null>(null);

  // No scrollbar inside the prose: the field is the paragraph, so it takes the
  // height the paragraph would have.
  useEffect(() => {
    for (const el of [body.current, criteria.current]) {
      if (!el) continue;
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [description, acceptance]);

  useEffect(() => {
    setSummary(ticket.summary);
    setDescription(ticket.description_text);
    setPoints(ticket.story_points === null ? '' : fmtPoints(ticket.story_points));
    setType(ticket.type);
    setState(ticket.state);
    setAssignee(ticket.assignee);
    setAcceptance(ticket.acceptance_text);
  }, [ticket.key, ticket.summary, ticket.description_text, ticket.story_points, ticket.type, ticket.state, ticket.assignee, ticket.acceptance_text]);

  const submit = (): void => {
    const edit: TicketEdit = {};
    const trimmed = summary.trim();
    if (trimmed && trimmed !== ticket.summary) edit.summary = trimmed;
    if (description !== ticket.description_text) edit.description = description;
    const parsed = Number.parseFloat(points);
    if (points.trim() !== '' && !Number.isNaN(parsed) && parsed !== ticket.story_points) edit.points = parsed;
    if (type !== ticket.type) edit.type = type;
    if (state !== ticket.state) edit.state = state;
    if (assignee !== ticket.assignee) edit.assignee = assignee;
    if (acceptance !== ticket.acceptance_text) edit.acceptance = acceptance;
    onCancel();
    if (Object.keys(edit).length) onSave(edit);
  };

  return (
    <>
      <input
        className={cx(styles['tkSummary'], styles['editTitle'])}
        aria-label="Summary"
        value={summary}
        onInput={(event) => setSummary((event.target as HTMLInputElement).value)}
      />

      <dl className={styles['props']}>
        <PropPicker label="Type" value={type} options={options.types} onChange={setType} />
        <PropPicker label="Status" value={state} options={options.states} onChange={setState} />
        <PropPicker label="Assignee" value={assignee} options={options.assignees} onChange={setAssignee} />
        <div className={styles['prop']}>
          <dt className={styles['propLabel']}>Points</dt>
          <dd className={styles['propValue']}>
            <input
              className={styles['editPoints']}
              aria-label="Story points"
              inputMode="decimal"
              value={points}
              onInput={(event) => setPoints((event.target as HTMLInputElement).value)}
            />
          </dd>
        </div>
      </dl>

      <textarea
        ref={body}
        className={cx(styles['desc'], styles['editBody'])}
        aria-label="Description"
        rows={1}
        value={description}
        onInput={(event) => setDescription((event.target as HTMLTextAreaElement).value)}
      />

      <Eyebrow className={styles['bodyLabel']}>Acceptance criteria</Eyebrow>
      <textarea
        ref={criteria}
        className={cx(styles['desc'], styles['editBody'])}
        aria-label="Acceptance criteria"
        rows={1}
        value={acceptance}
        onInput={(event) => setAcceptance((event.target as HTMLTextAreaElement).value)}
      />

      <p className={styles['editNote']}>Saving writes to the tracker, not just this board.</p>

      <div className={styles['editActions']}>
        <Button tone="primary" onClick={submit}>
          Save
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </>
  );
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

/** One labelled fact about the ticket. Omitted entirely when it has no value. */
function Prop({ label, value, tone }: { label: string; value?: string | null; tone?: 'ok' }) {
  if (!value) return null;
  return (
    <div className={styles['prop']}>
      <dt className={styles['propLabel']}>{label}</dt>
      <dd className={cx(styles['propValue'], tone === 'ok' && styles['propOk'])}>{value}</dd>
    </div>
  );
}

function TicketBody({ ticket, tag, onEdit }: BodyProps) {
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
        {onEdit ? (
          <Button size="s" shape="bare" aria-label="Edit this ticket" onClick={onEdit}>
            <Icon name="pencil" size={12} /> Edit
          </Button>
        ) : null}
      </div>

      <h1 className={styles['tkSummary']}>{ticket.summary}</h1>

      <dl className={styles['props']}>
        <Prop label="Type" value={ticket.type} />
        <Prop label="Status" value={ticket.state} />
        <Prop label="Assignee" value={ticket.assignee} />
        <Prop label="Points" value={fmtPoints(ticket.story_points)} />
        {ticket.estimated ? (
          <Prop label="Estimated" value={fmtPoints(ticket.final_points)} tone="ok" />
        ) : null}
      </dl>

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
  /** Host-only: open the ticket for editing. */
  onEdit(): void;
  /** The ticket is open for editing in place. */
  editing: boolean;
  /** Choices for the pickers, gathered from the board's tickets. */
  options: TicketOptions;
  onSaveEdit(edit: TicketEdit): void;
  onCancelEdit(): void;
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
  onEdit,
  editing,
  options,
  onSaveEdit,
  onCancelEdit,
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
      {editing ? (
        <>
          <div className={styles['tkrow']}>
            <span className={styles['key']}>{ticket.key}</span>
            <span className={styles['phaseTag']}>editing</span>
          </div>
          <TicketForm ticket={ticket} options={options} onSave={onSaveEdit} onCancel={onCancelEdit} />
        </>
      ) : (
        <TicketBody ticket={ticket} tag={tag} onEdit={isHost ? onEdit : undefined} />
      )}
    </section>
  );
}
