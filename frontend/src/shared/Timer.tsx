/**
 * The ceremony timer: a readout in the toolbar, presets in a popover.
 *
 * Only the **host** may start or stop it — a timebox everyone can reset is not
 * a timebox. Guests get the readout, which is the part that matters to them.
 *
 * Two accessibility fixes over the old markup:
 *
 * * The readout is an `aria-live="polite"` region that announces at the
 *   minute, and at the last ten seconds. Announcing every second would make the
 *   page unusable with a screen reader; announcing nothing means a blind
 *   participant has no idea the timebox is nearly up.
 * * `#timer-btn.done .rd` used to run `animation: blink 1s steps(2) infinite`
 *   with no `prefers-reduced-motion` guard anywhere in the retro stylesheet — a
 *   literal flashing element. There is no finished state on the toolbar at all
 *   now: the confetti and the alarm announce the end, and the readout simply
 *   goes.
 */

import { useEffect, useState, type ReactNode } from 'react';

import { fmtClock } from '../runtime/format';
import { cx } from '../runtime/cx';
import { Button } from './Button';
import styles from './shared.module.css';

/** Preset lengths, in seconds. The usual retro/poker timeboxes. */
export const TIMER_PRESETS: readonly number[] = [60, 180, 300, 600];

export interface TimerReadoutProps {
  /** Seconds left, or `null` when no timer is running. */
  remaining: number | null;
  className?: string | undefined;
}

/** How long the readout is held to fold away. Matches `readoutOut` in the CSS. */
const READOUT_EXIT_MS = 200;

export function TimerReadout({ remaining, className }: TimerReadoutProps) {
  // A finished timer says nothing here: the confetti and the alarm already said
  // it, and a strip of type reading "time's up" stays on the toolbar long after
  // the moment has passed.
  const live = remaining !== null && remaining > 0;
  const [shown, setShown] = useState<number | null>(live ? remaining : null);
  const [leaving, setLeaving] = useState(false);

  // Held for one beat on the way out, and folded — the readout is part of the
  // dock's width, so dropping it on the same frame snaps the whole dock in.
  useEffect(() => {
    if (live) {
      setShown(remaining);
      setLeaving(false);
      return;
    }
    if (shown === null) return;
    setLeaving(true);
    const timer = setTimeout(() => {
      setShown(null);
      setLeaving(false);
    }, READOUT_EXIT_MS);
    return () => clearTimeout(timer);
  }, [live, remaining, shown]);

  if (shown === null) return null;
  // Announce at each whole minute and through the last ten seconds; stay quiet
  // in between. `aria-live` reads the region when its text changes, so the
  // switch between 'polite' and 'off' is what throttles it.
  const announce = shown <= 10 || shown % 60 === 0;

  return (
    <span
      className={cx(styles['timerReadout'], leaving && styles['readoutOut'], className)}
      aria-live={leaving ? 'off' : announce ? 'polite' : 'off'}
      aria-atomic="true"
    >
      {fmtClock(shown)}
    </span>
  );
}

export interface TimerControlsProps {
  running: boolean;
  onStart(seconds: number): void;
  onStop(): void;
  presets?: readonly number[];
  className?: string | undefined;
}

/** The popover body. Host-only — do not render it for a guest. */
export function TimerControls({
  running,
  onStart,
  onStop,
  presets = TIMER_PRESETS,
  className,
}: TimerControlsProps) {
  const [custom, setCustom] = useState('');

  const startCustom = (): void => {
    const minutes = Number.parseInt(custom, 10);
    if (Number.isFinite(minutes) && minutes > 0) {
      onStart(minutes * 60);
      setCustom('');
    }
  };

  return (
    <div className={cx(styles['panelForm'], className)}>
      <div className={styles['field']}>
        <span className={styles['fieldLabel']}>Length</span>
        <div className={styles['presetRow']} role="group" aria-label="Preset lengths">
          {presets.map((seconds) => (
            <button key={seconds} type="button" className={styles['preset']} onClick={() => onStart(seconds)}>
              {seconds % 60 === 0 ? `${seconds / 60}m` : `${seconds}s`}
            </button>
          ))}
        </div>
      </div>

      <div className={styles['field']}>
        <span className={styles['fieldLabel']}>Or a number of minutes</span>
        <div className={styles['inputRow']}>
          <input
            type="number"
            min="1"
            max="120"
            inputMode="numeric"
            className={styles['numberInput']}
            placeholder="10"
            aria-label="Custom length in minutes"
            value={custom}
            onInput={(event) => setCustom((event.target as HTMLInputElement).value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') startCustom();
            }}
          />
          {/* One action, and it is whichever one there is to take: a Stop
              elsewhere on the panel is a second thing to find. */}
          {running ? (
            <Button tone="danger" className={styles['inputAction']} onClick={onStop}>
              Stop
            </Button>
          ) : (
            <Button tone="primary" className={styles['inputAction']} onClick={startCustom}>
              Start
            </Button>
          )}
        </div>
      </div>

    </div>
  );
}

/** Full-viewport canvas for the finish celebration. Attach the useConfetti ref. */
export function ConfettiCanvas({ canvasRef }: { canvasRef: (el: HTMLCanvasElement | null) => void }): ReactNode {
  return <canvas ref={canvasRef} className={styles['confetti']} aria-hidden="true" />;
}
