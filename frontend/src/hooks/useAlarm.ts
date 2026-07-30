/**
 * The timer-finished chime — four two-tone beeps via the Web Audio API.
 *
 * Synthesised rather than shipped as an audio file for a reason that is easy to
 * miss: every one of these pages must be a *single self-contained document*.
 * An `<audio src>` needs a second file (impossible over `file://` for an export)
 * or a base64 data URI (tens of kilobytes inlined into every page). Four
 * oscillators cost nothing and sound the same everywhere.
 *
 * Ported verbatim in behaviour from the identical block in both board files,
 * with reduced-motion respected: someone who has asked the system to calm down
 * does not want a sudden alarm either, and this is the only audible thing
 * either board does unprompted.
 */

import { useCallback } from 'react';

const BEEPS = 4;
const BEEP_GAP = 0.4;
const TONES = [880, 1175];
const PEAK_GAIN = 0.25;

type AudioContextCtor = typeof AudioContext;

function audioContextCtor(): AudioContextCtor | undefined {
  // webkitAudioContext is still the only constructor on older iOS Safari, which
  // is a meaningful share of the phones a tunnel link gets opened on.
  const w = window as Window & { webkitAudioContext?: AudioContextCtor };
  return window.AudioContext ?? w.webkitAudioContext;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Returns a `fire()` that plays the chime. Safe to call when audio is unavailable. */
export function useAlarm(): () => void {
  return useCallback(() => {
    if (prefersReducedMotion()) return;
    const Ctor = audioContextCtor();
    if (!Ctor) return;

    let ctx: AudioContext;
    try {
      ctx = new Ctor();
    } catch {
      return; // no audio device, or a policy that forbids constructing one
    }
    // Autoplay policy suspends a context created without a user gesture. The
    // timer finishing is not a gesture, so resume() is what makes the alarm
    // audible for anyone who has since clicked anywhere on the page.
    if (ctx.state === 'suspended') void ctx.resume();

    const start = ctx.currentTime;
    for (let beep = 0; beep < BEEPS; beep += 1) {
      for (const frequency of TONES) {
        const oscillator = ctx.createOscillator();
        const gain = ctx.createGain();
        oscillator.type = 'square';
        oscillator.frequency.value = frequency;
        const at = start + beep * BEEP_GAP;
        // Exponential ramps from a near-zero floor, not setValueAtTime steps:
        // a square wave switched on at full amplitude produces an audible click.
        // exponentialRampToValueAtTime also cannot accept 0, hence 0.0001.
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(PEAK_GAIN, at + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.3);
        oscillator.connect(gain);
        gain.connect(ctx.destination);
        oscillator.start(at);
        oscillator.stop(at + 0.32);
      }
    }

    // Browsers cap how many AudioContexts a page may hold; leaking one per
    // finished timer eventually makes the alarm stop working for the session.
    setTimeout(() => void ctx.close().catch(() => {}), (BEEPS + 1) * BEEP_GAP * 1000);
  }, []);
}
