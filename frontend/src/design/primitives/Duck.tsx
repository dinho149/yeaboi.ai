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
  /** Rendered width in px. The sprite is 128px, so 64 is the 2x-crisp size. */
  size?: number;
  className?: string | undefined;
}

export function Duck({ state = 'idle', size = 64, className }: DuckProps) {
  return (
    <div
      className={cx(styles['duck'], className)}
      data-state={state}
      style={{ width: `${size}px` }}
      aria-hidden="true"
    >
      <img className={styles['base']} src={baseSrc} alt="" draggable={false} />
      <img className={styles['wing']} src={wingSrc} alt="" draggable={false} />
      <img className={styles['glasses']} src={glassesSrc} alt="" draggable={false} />
      {state === 'offline' ? <span className={styles['zzz']}>z</span> : null}
    </div>
  );
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
