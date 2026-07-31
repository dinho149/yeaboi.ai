/**
 * The timer-finished chime — a three-note bell arpeggio via the Web Audio API.
 *
 * Synthesised rather than shipped as an audio file for a reason that is easy to
 * miss: every one of these pages must be a *single self-contained document*.
 * An `<audio src>` needs a second file (impossible over `file://` for an export)
 * or a base64 data URI (tens of kilobytes inlined into every page). A handful of
 * oscillators cost nothing and sound the same everywhere.
 *
 * ## Why it is not four square-wave beeps any more
 *
 * It used to be: four bursts of an 880 Hz + 1175 Hz square-wave chord at 0.25
 * gain, ported verbatim from the pre-React board. A square wave is every odd
 * harmonic at full strength, two of them at once beat against each other, and
 * four repeats read as an *alarm* — the sound a fire panel makes. What the end
 * of a timebox wants is a chime: audible across a room, over in two seconds,
 * and not unpleasant to hear twenty times in an hour-long retro.
 *
 * So: sine partials instead, three rising notes that ring into each other, and
 * a struck-then-decaying envelope. {@link PARTIALS} is what makes it read as a
 * bell rather than an organ — a real bell's overtones are *inharmonic*, and 2.76
 * (the "nominal") is the classic ratio that gives the metallic edge. Octaves
 * alone sound like a synth pad.
 *
 * Reduced motion is respected: someone who has asked the system to calm down
 * does not want a sudden noise either, and this is the only thing either board
 * makes a sound about unprompted.
 */

import { useCallback } from 'react';

/** A major triad, rising. Each entry is [frequency in Hz, decay in seconds]. */
const NOTES: readonly (readonly [number, number])[] = [
  [880.0, 1.1], // A5
  [1108.73, 1.1], // C#6
  [1318.51, 1.9], // E6 — held, so the chime ends on a ring rather than a stop
];
/** Seconds between note onsets. Short enough that the three overlap and chord. */
const NOTE_GAP = 0.16;
/**
 * Partial ratios and their share of the peak, per note. 2.76 is inharmonic on
 * purpose — see the note above on why octaves alone sound like a synth.
 */
const PARTIALS: readonly (readonly [number, number])[] = [
  [1, 1],
  [2, 0.35],
  [2.76, 0.12],
];
/** Struck, not faded in: fast enough to sound like a strike, slow enough not to click. */
const ATTACK = 0.008;
const PEAK_GAIN = 0.18;
/** The floor an exponential ramp decays to — it cannot accept 0. */
const SILENT = 0.0001;

/** When the last partial stops sounding, measured from the first strike. */
const TAIL = (NOTES.length - 1) * NOTE_GAP + Math.max(...NOTES.map(([, decay]) => decay));

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
    // timer finishing is not a gesture, so resume() is what makes the chime
    // audible for anyone who has since clicked anywhere on the page.
    if (ctx.state === 'suspended') void ctx.resume();

    const start = ctx.currentTime;
    NOTES.forEach(([frequency, decay], index) => {
      const at = start + index * NOTE_GAP;
      for (const [ratio, share] of PARTIALS) {
        const oscillator = ctx.createOscillator();
        const gain = ctx.createGain();
        oscillator.type = 'sine';
        oscillator.frequency.value = frequency * ratio;
        // Exponential ramps from a near-zero floor, not setValueAtTime steps:
        // a wave switched on at full amplitude produces an audible click.
        // exponentialRampToValueAtTime also cannot accept 0, hence SILENT.
        gain.gain.setValueAtTime(SILENT, at);
        gain.gain.exponentialRampToValueAtTime(PEAK_GAIN * share, at + ATTACK);
        // The whole decay in one ramp is what makes it a bell rather than a
        // beep: the loudest instant is the strike, and the rest is it dying.
        gain.gain.exponentialRampToValueAtTime(SILENT, at + decay);
        oscillator.connect(gain);
        gain.connect(ctx.destination);
        oscillator.start(at);
        oscillator.stop(at + decay);
      }
    });

    // Browsers cap how many AudioContexts a page may hold; leaking one per
    // finished timer eventually makes the chime stop working for the session.
    setTimeout(() => void ctx.close().catch(() => {}), (TAIL + 0.4) * 1000);
  }, []);
}
