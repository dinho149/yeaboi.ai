/**
 * The open floor: the lowest and highest voters argue, on the clock.
 *
 * The premise is that the interesting information in a split vote is not the
 * median, it is *why* two people who read the same ticket landed three cards
 * apart. So the board picks the extremes, gives each a timed turn, records what
 * they say, transcribes it, and hands it to the AI for a verdict.
 *
 * ## What this component owns
 *
 * The rendering of four states — live, transcribing, done, failed — and the mic
 * consent button. The recording choreography itself is {@link useDuelMic},
 * because it outlives any one render and has to release hardware on unmount.
 *
 * ## The honesty rule
 *
 * A duel with no mic anywhere produces no transcript, and the host will not find
 * that out until they close the floor and get an empty verdict. So the header
 * says which it is, every time: RECORDING, or "no mic recording — the debate
 * won't be transcribed". A silent failure here wastes the one part of the
 * ceremony people find genuinely novel.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { cx } from '../runtime/cx';
import { rememberFloor, seatFace } from './seats';
import { fmtClock } from '../runtime/format';
import type { DuelSlice } from '../types/board';
import styles from './poker.module.css';
import { Icon } from '../design/primitives';
import { Button } from '../shared';

export interface DuelProps {
  duel: DuelSlice;
  /** Seconds left on the turn, shown between the two of them. */
  remaining: number | null;
  isHost: boolean;
  onNextTurn(): void;
  onCloseDuel(): void;
}

/** How long a duelist takes to get out of their chair, in ms. */
const SEAT_MS = 620;
const SEAT_EASE = 'cubic-bezier(0.32, 0.94, 0.3, 1)';

/**
 * Walk a duelist from their chair to the floor.
 *
 * The face is what travels — it leaves the seat's coordinates and arrives at the
 * panel's — and the panel fades up around it once it lands. The alternative, and
 * what this replaced, was flying the whole panel from the seat: correct in its
 * arithmetic and wrong to look at, because the thing that moved was a rectangle
 * the size of the floor rather than the person.
 *
 * The chair is gone from the DOM by the time this runs, so the start is the box
 * the table recorded on its last layout. Once per pairing: a re-render mid-turn
 * must not send them back and out again.
 */
function useSeatEntrance(
  card: { current: HTMLDivElement | null },
  face: { current: HTMLSpanElement | null },
  name: string
): void {
  const flown = useRef('');

  useLayoutEffect(() => {
    const node = face.current;
    if (!name || !node) return;
    // Recorded every layout, so the table can fly them home again.
    rememberFloor(name, node);
    if (flown.current === name) return;
    flown.current = name;
    if (typeof node.animate !== 'function') return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const from = seatFace(name);
    const here = node.getBoundingClientRect();
    if (!from || !here.width) return;
    const dx = from.x - (here.left + here.width / 2);
    const dy = from.y - (here.top + here.height / 2);
    node.animate(
      [
        { transform: `translate(${dx}px, ${dy}px) scale(${from.width / here.width})` },
        { transform: 'none' },
      ],
      { duration: SEAT_MS, easing: SEAT_EASE }
    );
    // The panel is not what moves; it arrives around them.
    card.current?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: SEAT_MS, easing: 'ease-out' });
  }, [card, face, name]);
}

function Duelist({ duel, role }: { duel: DuelSlice; role: 'low' | 'high' }) {
  const person = duel[role];
  const speaking = duel.status === 'live' && duel.turn === role;
  const card = useRef<HTMLDivElement>(null);
  const face = useRef<HTMLSpanElement>(null);
  useSeatEntrance(card, face, duel.status === 'live' ? person.name : '');
  return (
    <div ref={card} className={cx(styles['duelist'], speaking && styles['duelistSpeaking'])}>
      <span ref={face} className={styles['duelFace']} aria-hidden="true">
        {person.avatar || <Icon name="user" size={14} />}
      </span>
      <span className={styles['duelName']}>{person.name}</span>
      <span className={styles['duelValue']}>
        argues for <b>{person.value}</b>
      </span>
      {speaking ? <span className={styles['duelFloor']}>has the floor</span> : null}
      {duel.recording[role] ? (
        <span className={styles['duelMic']}>
          <Icon name="mic" /> mic on
        </span>
      ) : null}
    </div>
  );
}

/** How long the announcement holds the screen, in ms. */
const TURN_MS = 2400;

/**
 * "You're up", once, over the whole board.
 *
 * A line inside the floor was the wrong shape for it: the floor is one tab of
 * three, so the person whose turn had just started could be reading the spread
 * and never see it — and once seen there is nothing more to do with it, yet it
 * stayed for the length of the turn.
 *
 * A portal, because it belongs to the screen rather than to the panel it is
 * declared in, and the panel it is declared in is `visibility: hidden` whenever
 * another tab is showing.
 */
function YourTurn({ on }: { on: boolean }) {
  const [showing, setShowing] = useState(false);

  useEffect(() => {
    if (!on) return undefined;
    setShowing(true);
    const timer = window.setTimeout(() => setShowing(false), TURN_MS);
    return () => window.clearTimeout(timer);
  }, [on]);

  if (!showing) return null;
  return createPortal(
    // `role="status"` rather than `alert`: it is your turn, not an error, and
    // alert would interrupt whatever a screen reader was mid-sentence on.
    <div className={styles['youupWrap']} role="status">
      <p className={styles['youup']}>You&rsquo;re up — make your case!</p>
    </div>,
    document.body
  );
}

export function Duel({ duel, remaining, isHost, onNextTurn, onCloseDuel }: DuelProps) {
  if (duel.status === 'transcribing') {
    return (
      <div className={styles['duel']} role="status">
        <p className={styles['duelBody']}>
          Transcribing the debate… (the first run may download the speech model)
        </p>
      </div>
    );
  }

  if (duel.status === 'done') {
    return (
      <div className={styles['duel']}>
        <div className={styles['duelHead']}>
{duel.low.name} vs {duel.high.name}
        </div>
        <p className={styles['duelTranscript']}>{duel.transcript}</p>
      </div>
    );
  }

  if (duel.status !== 'live') {
    return (
      <div className={styles['duel']}>
        <p className={styles['duelBody']}>{duel.error || 'Recording failed.'}</p>
      </div>
    );
  }

  const myTurn = duel.mine_role !== '' && duel.turn === duel.mine_role;

  return (
    <div className={styles['duel']}>

      <div className={styles['dualrow']}>
        <Duelist duel={duel} role="low" />
        {/* Between them, because that is what all of it is about: the clock is
            counting down whose turn it is, and the two controls hand the floor
            over and close it. */}
        <div className={styles['vs']}>
          {remaining === null ? (
            <span aria-hidden="true">VS</span>
          ) : (
            <span className={styles['vsClock']}>{fmtClock(remaining)}</span>
          )}
          {isHost ? (
            <div className={styles['vsActs']}>
              <Button size="s" disabled={duel.turn !== 'low'} title="Hand the floor to the high voter" onClick={onNextTurn}>
                Next turn ›
              </Button>
              <Button size="s" onClick={onCloseDuel}>
                Close the floor
              </Button>
            </div>
          ) : null}
        </div>
        <Duelist duel={duel} role="high" />
      </div>

      <YourTurn on={myTurn} />

      {duel.recording.host ? <p className={styles['hint']}>Host room mic is recording the debate.</p> : null}
    </div>
  );
}
