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

import { cx } from '../runtime/cx';
import { fmtClock } from '../runtime/format';
import type { DuelSlice } from '../types/board';
import type { DuelMic } from './useDuelMic';
import styles from './poker.module.css';
import { Icon } from '../design/primitives';

export interface DuelProps {
  duel: DuelSlice;
  mic: DuelMic;
  /** Seconds left on the turn, shown between the two of them. */
  remaining: number | null;
}

function Duelist({ duel, role }: { duel: DuelSlice; role: 'low' | 'high' }) {
  const person = duel[role];
  const speaking = duel.status === 'live' && duel.turn === role;
  return (
    <div className={cx(styles['duelist'], speaking && styles['duelistSpeaking'])}>
      <span className={styles['duelFace']} aria-hidden="true">
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

export function Duel({ duel, mic, remaining }: DuelProps) {
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

  const anyRecording = duel.recording.host || duel.recording.low || duel.recording.high;
  const mine = duel.mine_role;
  const myTurn = mine !== '' && duel.turn === mine;

  return (
    <div className={styles['duel']}>
      <div className={styles['duelHead']}>
        {anyRecording ? (
          <span className={styles['recind']}>
            <span className={styles['recDot']} aria-hidden="true" />
            RECORDING
          </span>
        ) : (
          <span className={styles['norec']}>no mic recording — the debate won&rsquo;t be transcribed</span>
        )}
      </div>

      <div className={styles['dualrow']}>
        <Duelist duel={duel} role="low" />
        {/* The clock stands between them, because that is what it counts down:
            whose turn it is, not how long the board has been open. */}
        {remaining === null ? (
          <span className={styles['vs']} aria-hidden="true">
            VS
          </span>
        ) : (
          <span className={cx(styles['vs'], styles['vsClock'])}>{fmtClock(remaining)}</span>
        )}
        <Duelist duel={duel} role="high" />
      </div>

      {/* `role="status"` rather than `alert`: it is your turn, not an error, and
          alert would interrupt whatever a screen reader was mid-sentence on. */}
      {myTurn ? (
        <p className={styles['youup']} role="status">
          You&rsquo;re up — make your case!
        </p>
      ) : null}

      {mine !== '' && !mic.armed ? (
        <div className={styles['micRow']}>
          {mic.capable ? (
            <>
              <button type="button" className={styles['micBtn']} onClick={() => void mic.enable()}>
                <Icon name="mic" /> Start my mic
              </button>
              <span className={styles['hint']}>record your own turn — attributed to you in the transcript</span>
            </>
          ) : (
            <span className={styles['hint']}>
              {/* Not a failure to work around: getUserMedia needs a secure
                  context. Every way of reaching this board now is one — the
                  host is on localhost, everyone else on the HTTPS tunnel — so
                  this branch is a browser that refused rather than a connection
                  that can't. Worded as "this connection" rather than naming the
                  scheme, because the person reading it opened a link someone
                  sent them, and the bundle guard forbids a literal scheme
                  anywhere in the JS regardless. */}
              Your browser can&rsquo;t record on this connection — the room mic covers you.
            </span>
          )}
          {mic.error ? <span className={styles['hint']}>{mic.error}</span> : null}
        </div>
      ) : null}

      {duel.recording.host ? <p className={styles['hint']}>Host room mic is recording the debate.</p> : null}
    </div>
  );
}
