/**
 * The yeaboi duck, as a live indicator.
 *
 * The mascot already existed everywhere except the surfaces teammates actually
 * see: six PNGs, two hand-drawn ASCII sprites in the TUI screensaver, a colour
 * spec in `_screensaver.py`, a `reporting/branding.py`, and three animated
 * appearances on yeaboi.ai. The tunnel pages showed `🤙` instead.
 *
 * Putting it here is not only decoration. A live board's one job is to be live,
 * and the board had no way at all of saying so — a peer's card simply appeared,
 * and a dead long-poll looked exactly like a quiet room. The duck is where that
 * state became visible: it flaps when a card lands and it falls asleep when the
 * connection drops. It is the reconnect indicator, and it happens to be the
 * brand.
 *
 * ## Structure
 *
 * Three layers, stacked, each owning its own transform — ported from
 * `docs/assets/landing.css:425-532`, where the timings are already tuned. They
 * are separate elements precisely so waddle, wing-flap, glasses-bob and the
 * startle never fight over `transform` on one node.
 *
 * ## Reduced motion
 *
 * The docs site hides the duck outright under `prefers-reduced-motion`, which
 * it can afford to — there it is pure delight. Here it carries connection
 * status, so it stays visible and the states are expressed as **plain
 * properties rather than animations**: the global guard in tokens.css flattens
 * every animation to 0.01ms, so anything encoded as a keyframe disappears.
 * `locked` and `offline` therefore set a static transform, and `offline` adds a
 * literal "z" that survives regardless.
 *
 * ## Accessibility
 *
 * The whole rig is `aria-hidden`. That is deliberate and not a shortcut: every
 * state it shows is already announced by real UI — the lock banner is a
 * `role="alert"`, the reconnecting notice is in the toolbar subtitle, the timer
 * readout is `aria-live`. Giving the duck its own label would double-announce
 * all of it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { cx } from '../../runtime/cx';
import baseSrc from '../../assets/duck/base.png';
import glassesSrc from '../../assets/duck/glasses.png';
import wingSrc from '../../assets/duck/wing.png';
import styles from './duck.module.css';

/** Unprompted mannerisms, played on a timer while nothing else is happening. */
export type DuckIdle = 'tilt' | 'peek' | 'preen';

/** How long each mannerism lasts, in ms. Matches duck.module.css. */
const IDLE_MS: Record<DuckIdle, number> = {
  tilt: 1400,
  peek: 1200,
  preen: 1600,
};

const IDLE_KINDS = Object.keys(IDLE_MS) as DuckIdle[];

/** Gap between mannerisms. Wide, and random inside the range, so the duck never
 *  reads as a metronome — a fixed interval is what makes a loop look like a GIF. */
const IDLE_MIN_MS = 6_000;
const IDLE_MAX_MS = 14_000;

/** States the duck holds until something changes them. */
export type DuckRest = 'idle' | 'urgent' | 'locked' | 'offline';
/** One-shot reactions, played once and then dropped back to the resting state. */
export type DuckPulse = 'card' | 'joined' | 'startled';
export type DuckState = DuckRest | DuckPulse;

/** How long each pulse occupies the duck, in ms. Matches duck.module.css. */
const PULSE_MS: Record<DuckPulse, number> = {
  card: 900,
  joined: 1400,
  startled: 1350,
};

export interface DuckProps {
  state?: DuckState;
  /**
   * Music is playing — the duck dances to it.
   *
   * Not a `DuckState`, because it is a mood and every state is a report. Kept
   * apart so a dance can never mask a dead connection, and so the duck can do
   * both at once.
   */
  jamming?: boolean;
  /** Rendered width in px. The sprite is 128px, so 64 is the 2x-crisp size. */
  size?: number;
  className?: string | undefined;
}

export function Duck({ state = 'idle', jamming = false, size = 64, className }: DuckProps) {
  // Only when nothing else is going on. An idle mannerism during a startle or a
  // reconnect would be two animations arguing over the same layer, and the one
  // that matters would be the one that lost — and a duck that already has music
  // to move to does not need something to do.
  const idle = useDuckIdle(state === 'idle' && !jamming);
  const arriving = useDuckArrival();

  return (
    <div
      className={cx(styles['duck'], className)}
      data-state={state}
      data-jam={jamming ? 'true' : undefined}
      data-enter={arriving ? 'true' : undefined}
      data-idle={idle ?? undefined}
      style={{ width: `${size}px` }}
      aria-hidden="true"
    >
      {/* The body layer exists so the entrance and the resting bob are not both
          transforms on the same node. `.duck` owns arrival and the state
          transforms; everything continuous lives in here. */}
      <div className={styles['body']}>
        <img className={styles['base']} src={baseSrc} alt="" draggable={false} />
        <img className={styles['wing']} src={wingSrc} alt="" draggable={false} />
        <img className={styles['glasses']} src={glassesSrc} alt="" draggable={false} />
      </div>
      {/* Both resting states are a nap: the connection is gone, or the room is
          closed. What tells them apart is colour — `offline` is drained. */}
      {state === 'offline' || state === 'locked' ? <span className={styles['zzz']}>z</span> : null}
    </div>
  );
}

/** How long `duck-waddle-in` runs, in ms. Matches duck.module.css. */
const ENTER_MS = 1400;

/**
 * True for the length of the arrival, once per mount.
 *
 * The entrance is a mount, not a state. Left on `.duck`'s base rule, the states
 * that cancel it to hold a static transform re-arm it on the way out — so
 * unlocking a board replayed the whole waddle instead of the duck simply turning
 * back around.
 */
function useDuckArrival(): boolean {
  const [arriving, setArriving] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), ENTER_MS);
    return () => clearTimeout(timer);
  }, []);

  return arriving;
}

/**
 * Play an occasional unprompted mannerism while `enabled`.
 *
 * The constant wing-flap says "this page is running". It does not say "there is
 * someone here" — for that the duck has to do something you did not ask for and
 * cannot predict, which is why the gap is randomised rather than fixed.
 *
 * Owned by {@link Duck} rather than exposed to callers, so the static export
 * masthead and the join gate get it without wiring anything up.
 *
 * Nothing here is gated on `prefers-reduced-motion`: the global guard in
 * tokens.css flattens the animations to nothing, so the attribute changes and no
 * motion results. That is the correct outcome, and it keeps this hook from being
 * a second place where the reduced-motion rule is decided.
 */
export function useDuckIdle(enabled: boolean): DuckIdle | null {
  const [idle, setIdle] = useState<DuckIdle | null>(null);

  useEffect(() => {
    if (!enabled) {
      setIdle(null);
      return;
    }

    let timer: ReturnType<typeof setTimeout>;

    const schedule = () => {
      timer = setTimeout(
        () => {
          const kind = IDLE_KINDS[Math.floor(Math.random() * IDLE_KINDS.length)] ?? 'tilt';
          setIdle(kind);
          timer = setTimeout(() => {
            setIdle(null);
            schedule();
          }, IDLE_MS[kind]);
        },
        IDLE_MIN_MS + Math.random() * (IDLE_MAX_MS - IDLE_MIN_MS)
      );
    };

    schedule();
    return () => clearTimeout(timer);
  }, [enabled]);

  return idle;
}

/**
 * Hold a one-shot reaction for the length of its animation, then fall back.
 *
 * The alternative — letting the caller pass `state="card"` and clear it — reads
 * simpler but cannot replay: two cards arriving in a row leave the prop at
 * `"card"` throughout, React re-renders nothing, and the CSS animation never
 * restarts. So the pulse is owned here, and `signal()` is safe to call at any
 * rate; a second card interrupts the first rather than being swallowed.
 *
 * `resting` is live: if the connection drops mid-flap, the duck lands asleep.
 */
export function useDuckPulse(resting: DuckRest = 'idle'): [DuckState, (pulse: DuckPulse) => void] {
  const [pulse, setPulse] = useState<DuckPulse | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const signal = useCallback((next: DuckPulse) => {
    if (timer.current !== null) clearTimeout(timer.current);
    // Drop to null first so re-signalling the *same* pulse still restarts the
    // animation — React bails out of a set that does not change the value.
    setPulse(null);
    timer.current = setTimeout(() => {
      setPulse(next);
      timer.current = setTimeout(() => setPulse(null), PULSE_MS[next]);
    }, 0);
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    []
  );

  // A resting state that means something — the board is locked, or we have
  // lost the server — outranks a decorative flap. Only `idle` yields.
  if (resting !== 'idle') return [resting, signal];
  return [pulse ?? resting, signal];
}
