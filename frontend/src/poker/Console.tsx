/**
 * The host's console: everything only the facilitator can do.
 *
 * Rendered only when this browser's URL carried the admin secret — and that is
 * **cosmetic only**. Every endpoint behind these buttons re-checks the secret
 * server-side with a constant-time compare, so a guest who reveals the console
 * in devtools gets a panel of buttons whose requests all come back 403.
 *
 * ## Why controls disable rather than disappear
 *
 * The console is a sticky column on desktop and a bottom sheet on a phone, and
 * a host uses it a hundred times in a session by muscle memory. Hiding a
 * control mid-round moves every control below it, so the button under your
 * thumb changes identity between the look and the tap. Everything therefore
 * keeps its place and greys out, and the collapsed mobile bar promotes the one
 * action that matters right now instead of reordering the panel.
 *
 * ## Finalize
 *
 * Prefilled from the suggestion once per reveal, and never re-prefilled while
 * the host is typing into it — a poll landing mid-keystroke used to overwrite
 * the number they were entering. It is also locked while a duel transcript is
 * still being produced: finalizing then would silently drop the debate from the
 * record, which is the one part of the round that cannot be reconstructed.
 */

import { useEffect, useState } from 'react';

import { Eyebrow } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { DuelSlice, PokerPhase, PokerState } from '../types/board';
import { fmtPoints } from './points';
import styles from './poker.module.css';

/** Turn lengths the host can open the floor with. Server clamps to 15..600s. */
export const DUEL_PRESETS = [60, 90, 120] as const;

export interface ConsoleProps {
  state: PokerState;
  /** Everyone present has voted — the cue to reveal. */
  allIn: boolean;
  onReveal(): void;
  onRevote(): void;
  onAskAi(): void;
  onOpenDuel(seconds: number): void;
  onNextTurn(): void;
  onCloseDuel(): void;
  onFinalize(points: number): void;
  onGoto(index: number): void;
  onEdit(): void;
  /** A refusal from the server, or a local validation message. */
  notice: string;
}

function duelLabel(duel: DuelSlice | null, phase: PokerPhase): string {
  if (phase === 'duel') return 'duel';
  if (duel?.status === 'transcribing') return 'transcribing';
  return phase;
}

export function Console({
  state,
  allIn,
  onReveal,
  onRevote,
  onAskAi,
  onOpenDuel,
  onNextTurn,
  onCloseDuel,
  onFinalize,
  onGoto,
  onEdit,
  notice,
}: ConsoleProps) {
  const { phase, duel, ai, ticket, ticket_index: index, ticket_count: count } = state;
  const revealed = phase === 'revealed';
  const dueling = phase === 'duel';
  const transcribing = duel?.status === 'transcribing';
  const hasTicket = Boolean(ticket);

  const [open, setOpen] = useState(false);
  const [duelOpen, setDuelOpen] = useState(false);
  const [points, setPoints] = useState('');
  const [typing, setTyping] = useState(false);

  /**
   * Prefill once per (ticket, phase), and never over a host mid-entry.
   *
   * `typing` is the guard: the moment the host touches the field it stops being
   * a suggestion and starts being their answer, and a poll landing a beat later
   * must not take it back.
   */
  useEffect(() => {
    if (!revealed) return;
    setTyping(false);
    setPoints(state.suggestion !== null ? fmtPoints(state.suggestion) : '');
    // Keyed on the round — `(index, phase)` — and deliberately not on
    // `suggestion` or `revealed`, both of which this body reads. The suggestion
    // moves when a late vote lands, and re-running then would overwrite the
    // host mid-entry, which is the exact thing being prevented. The omission is
    // the behaviour, not an oversight.
  }, [index, phase]);

  // The floor closing takes the preset picker with it — the console must not
  // jump between "choose a turn length" and the live controls.
  useEffect(() => {
    if (dueling) setDuelOpen(false);
  }, [dueling]);

  const canReveal = hasTicket && !revealed && !dueling;
  const finalizeReady = revealed && !transcribing;

  const submitFinalize = (): void => {
    const value = Number.parseFloat(points);
    if (Number.isNaN(value)) return;
    onFinalize(value);
  };

  return (
    <aside className={cx(styles['console'], open && styles['consoleOpen'])} data-phase={dueling ? 'duel' : phase}>
      {/* The collapsed bar is the whole console on a phone: where we are, and
          the single next action. Tapping the chevron opens the rest. */}
      <div className={styles['cbar']}>
        <span className={styles['cphase']}>{duelLabel(duel, phase)}</span>
        {dueling ? (
          <button type="button" className={cx(styles['cbtn'], styles['cbtnPrimary'])} onClick={onCloseDuel}>
            Close the floor
          </button>
        ) : transcribing ? (
          <button type="button" className={cx(styles['cbtn'], styles['cbtnPrimary'])} disabled>
            Transcribing…
          </button>
        ) : revealed ? (
          <button
            type="button"
            className={cx(styles['cbtn'], styles['cbtnPrimary'])}
            disabled={!finalizeReady}
            onClick={submitFinalize}
          >
            Save &amp; next{points ? ` · ${points}` : ''}
          </button>
        ) : (
          <button
            type="button"
            className={cx(styles['cbtn'], styles['cbtnPrimary'], allIn && styles['cbtnPulse'])}
            disabled={!canReveal}
            onClick={onReveal}
          >
            Reveal votes
          </button>
        )}
        <button
          type="button"
          className={styles['cbtn']}
          aria-expanded={open}
          aria-label={open ? 'Hide host controls' : 'Show host controls'}
          onClick={() => setOpen(!open)}
        >
          <span aria-hidden="true">⌃</span>
        </button>
      </div>

      <div className={styles['cbody']}>
        <div className={styles['cgroup']}>
          <Eyebrow>Round</Eyebrow>
          <button
            type="button"
            className={cx(styles['cbtn'], styles['cbtnPrimary'], allIn && styles['cbtnPulse'])}
            disabled={!canReveal}
            onClick={onReveal}
          >
            {revealed || dueling ? 'Revealed ✓' : 'Reveal votes'}
          </button>
          <button type="button" className={styles['cbtn']} disabled={!hasTicket || dueling} onClick={onRevote}>
            Re-vote
          </button>
        </div>

        <div className={styles['cgroup']}>
          <Eyebrow>Insight</Eyebrow>
          <button type="button" className={styles['cbtn']} disabled={!revealed || ai.pending} onClick={onAskAi}>
            <span aria-hidden="true">🤖</span> AI perspective
          </button>
          <button
            type="button"
            className={styles['cbtn']}
            disabled={!revealed || duel?.status === 'live' || transcribing}
            aria-expanded={duelOpen}
            title="Low vs high voter argue their estimates"
            onClick={() => setDuelOpen(!duelOpen)}
          >
            <span aria-hidden="true">🎤</span> Open the floor
          </button>

          {/* One reserved slot: the preset picker and the live controls swap
              inside it, so the console never grows or shrinks mid-round. */}
          <div className={styles['duelSlot']}>
            {duelOpen && !dueling ? (
              <div className={styles['duelInline']}>
                <span className={styles['clabel']}>Turn length</span>
                <div className={styles['seg']}>
                  {DUEL_PRESETS.map((seconds) => (
                    <button key={seconds} type="button" className={styles['preset']} onClick={() => onOpenDuel(seconds)}>
                      {seconds < 120 ? `${seconds}s` : `${seconds / 60}m`}
                    </button>
                  ))}
                </div>
                <p className={styles['chint']}>
                  Lowest voter argues first, then the highest. The debate is recorded &amp; transcribed for the
                  AI&rsquo;s verdict.
                </p>
              </div>
            ) : null}

            {dueling ? (
              <div className={styles['crow']}>
                <button
                  type="button"
                  className={styles['cbtn']}
                  disabled={duel?.turn !== 'low'}
                  title="Hand the floor to the high voter"
                  onClick={onNextTurn}
                >
                  Next turn ›
                </button>
                <button type="button" className={styles['cbtn']} onClick={onCloseDuel}>
                  Close the floor
                </button>
              </div>
            ) : null}
          </div>
        </div>

        <div className={styles['cgroup']}>
          <Eyebrow>Decision</Eyebrow>
          <p className={styles['readout']}>
            Team median <b>{revealed && state.median !== null ? fmtPoints(state.median) : '—'}</b>
          </p>
          <p className={styles['readout']}>
            AI suggests <b>{ai.suggested !== null ? fmtPoints(ai.suggested) : '—'}</b>
          </p>
          <div className={styles['finrow']}>
            <label className={styles['finlab']} htmlFor="poker-final-pts">
              Final points
            </label>
            <input
              id="poker-final-pts"
              type="number"
              min="0"
              step="1"
              placeholder="—"
              className={styles['finInput']}
              disabled={!finalizeReady}
              value={points}
              onInput={(event) => {
                setTyping(true);
                setPoints((event.target as HTMLInputElement).value);
              }}
            />
            {/* Where the number came from, so a host does not have to remember
                whether they typed it or the board suggested it. */}
            {!typing && points && finalizeReady ? <span className={styles['finSrc']}>median</span> : null}
          </div>
          <button
            type="button"
            className={cx(styles['cbtn'], styles['cbtnPrimary'])}
            disabled={!finalizeReady}
            title="Save to the board and move on"
            onClick={submitFinalize}
          >
            Save &amp; next
          </button>
        </div>

        <div className={styles['cgroup']}>
          <Eyebrow>Navigate</Eyebrow>
          <div className={styles['navrow']}>
            <button
              type="button"
              className={styles['cbtn']}
              aria-label="Previous ticket"
              disabled={!hasTicket || dueling || index <= 0}
              onClick={() => onGoto(index - 1)}
            >
              ‹
            </button>
            <span className={styles['cpos']}>{hasTicket ? `${index + 1} / ${count}` : '– / –'}</span>
            <button
              type="button"
              className={styles['cbtn']}
              aria-label="Next ticket"
              disabled={!hasTicket || dueling || index >= count - 1}
              onClick={() => onGoto(index + 1)}
            >
              ›
            </button>
            <button
              type="button"
              className={styles['cbtn']}
              aria-label="Edit the ticket on the board"
              disabled={!hasTicket || dueling}
              onClick={onEdit}
            >
              ✎
            </button>
          </div>

          {/* Tracker write failures live here rather than in a toast: they are
              the kind of thing a host needs to still be able to read after they
              have finished the ticket that caused them. */}
          {notice ? (
            <p className={styles['cnotice']} role="status">
              {notice}
            </p>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
