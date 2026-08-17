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

import { useRef } from 'react';

import { cx } from '../runtime/cx';
import { Announce, useArrival } from '../shared/board';
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

function Duelist({ duel, role }: { duel: DuelSlice; role: 'low' | 'high' }) {
  const person = duel[role];
  const speaking = duel.status === 'live' && duel.turn === role;
  const card = useRef<HTMLDivElement>(null);
  const face = useRef<HTMLSpanElement>(null);
  // The face travels out of the chair it was picked from; the panel arrives
  // around it. The chair is gone from the DOM by now — the kit kept its box.
  useArrival(face, {
    place: 'floor',
    from: 'table',
    name: duel.status === 'live' ? person.name : '',
    alsoFade: card,
  });
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

      <Announce when={myTurn}>You&rsquo;re up — make your case!</Announce>

      {duel.recording.host ? <p className={styles['hint']}>Host room mic is recording the debate.</p> : null}
    </div>
  );
}
