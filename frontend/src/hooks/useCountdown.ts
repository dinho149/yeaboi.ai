/**
 * The shared timer readout: a locally-ticking countdown against the server clock.
 *
 * Every snapshot carries `timer.now_epoch` — the server's clock at the moment it
 * built the response. Subtracting the browser's own clock gives an offset, and
 * from then on the countdown ticks locally. Two things fall out of that:
 *
 * * A laptop whose clock is minutes off still shows the same remaining time as
 *   everyone else's, which is the entire point of a shared ceremony timer.
 * * `now_epoch` can be excluded from the state ETag (`sharing/events.state_etag`
 *   does exactly that), because a stale one costs nothing. If it were *not*
 *   excluded, the tag would change on every single request and long-polling
 *   would collapse back into a busy poll.
 *
 * `onFinish` fires exactly once per timer, keyed on `end_epoch`. Keying on
 * "remaining hit zero" instead would re-fire the confetti and the alarm four
 * times a second for as long as the finished timer stayed on screen.
 */

import { useEffect, useRef, useState } from 'react';

/** The timer slice of a board snapshot. */
export interface TimerState {
  running: boolean;
  end_epoch?: number | null;
  now_epoch?: number | null;
  duration?: number | null;
}

const TICK_MS = 250;

export interface Countdown {
  /** Whole seconds left, or `null` when no timer is running. */
  remaining: number | null;
  /** True on the tick a running timer reaches zero and stays there. */
  finished: boolean;
}

export function useCountdown(timer: TimerState | null | undefined, onFinish?: () => void): Countdown {
  const offset = useRef(0);
  const syncedTo = useRef<number | null>(null);
  const firedFor = useRef<number | null>(null);
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;

  const running = Boolean(timer?.running && timer?.end_epoch);
  const endEpoch = timer?.end_epoch ?? 0;
  const nowEpoch = timer?.now_epoch ?? 0;

  // Re-sync only when a snapshot brings a *new* `now_epoch`, never on every
  // render. The offset is the difference between two clocks, so it is only
  // meaningful measured at the instant the reading arrived — and `now_epoch` is
  // excluded from the state ETag on purpose (see the note above), so it does
  // not move while the board is quiet. Recomputing it each render therefore
  // subtracts a *later* `Date.now()` from the same stale reading, sliding the
  // offset forward by exactly as much as real time advanced. Each tick would
  // then cancel out the last one and the readout would sit on the same second
  // for the whole timebox.
  if (nowEpoch && nowEpoch !== syncedTo.current) {
    syncedTo.current = nowEpoch;
    offset.current = nowEpoch - Date.now() / 1000;
  }

  const compute = (): number | null =>
    running ? Math.max(0, Math.round(endEpoch - (Date.now() / 1000 + offset.current))) : null;

  const [remaining, setRemaining] = useState<number | null>(compute);

  useEffect(() => {
    if (!running) {
      setRemaining(null);
      return;
    }
    const tick = (): void => {
      const left = Math.max(0, Math.round(endEpoch - (Date.now() / 1000 + offset.current)));
      setRemaining(left);
      if (left === 0 && firedFor.current !== endEpoch) {
        firedFor.current = endEpoch;
        onFinishRef.current?.();
      }
    };
    tick();
    // 250 ms, not 1000: at a one-second period the displayed second can lag the
    // real one by almost a full second, which is very visible when a room is
    // watching the last five tick down together.
    const id = setInterval(tick, TICK_MS);
    return () => clearInterval(id);
  }, [running, endEpoch]);

  return { remaining, finished: running && remaining === 0 };
}
