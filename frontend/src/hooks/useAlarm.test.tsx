/**
 * The timer chime.
 *
 * Untested until now, and it is exactly the kind of code that stays broken:
 * jsdom has no `AudioContext`, so the hook's own guard makes it a silent no-op
 * in every other test in this suite, and nobody hears a bundle in CI. The two
 * failures worth catching are both silent-in-development ones — an
 * `exponentialRampToValueAtTime(0, …)` throws only in a real browser (a
 * `RangeError` mid-chime, after the timer has already finished), and a context
 * that is never closed works fine until the tab hits the per-page cap and every
 * later timer ends in silence.
 *
 * The fake below records rather than sounds, so what is asserted is the
 * *schedule*: how many partials, at what frequencies, ramping to what.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useAlarm } from './useAlarm';

interface Ramp {
  value: number;
  time: number;
}

interface Voice {
  type: string;
  frequency: number;
  start: number;
  stop: number;
  ramps: Ramp[];
}

function fakeAudio() {
  const voices: Voice[] = [];
  const contexts: { closed: boolean; resumed: boolean }[] = [];
  const NOW = 100; // a non-zero currentTime, so a bug that ignores it shows up

  class FakeAudioContext {
    state = 'suspended';
    currentTime = NOW;
    destination = {} as AudioNode;
    private readonly record = { closed: false, resumed: false };

    constructor() {
      contexts.push(this.record);
    }

    resume(): Promise<void> {
      this.record.resumed = true;
      this.state = 'running';
      return Promise.resolve();
    }

    close(): Promise<void> {
      this.record.closed = true;
      return Promise.resolve();
    }

    createGain() {
      const voice = { ramps: [] as Ramp[] };
      return {
        _voice: voice,
        connect: () => {},
        gain: {
          setValueAtTime: (value: number, time: number) => void voice.ramps.push({ value, time }),
          exponentialRampToValueAtTime: (value: number, time: number) => void voice.ramps.push({ value, time }),
        },
      };
    }

    createOscillator() {
      const voice: Voice = { type: '', frequency: 0, start: 0, stop: 0, ramps: [] };
      voices.push(voice);
      return {
        set type(value: string) {
          voice.type = value;
        },
        frequency: {
          set value(hz: number) {
            voice.frequency = hz;
          },
        },
        // The gain node's ramps are what matter, and an oscillator is always
        // connected to exactly one — so fold them together here rather than
        // making the test walk a graph.
        connect: (node: { _voice?: { ramps: Ramp[] } }) => {
          if (node._voice) voice.ramps = node._voice.ramps;
        },
        start: (at: number) => void (voice.start = at),
        stop: (at: number) => void (voice.stop = at),
      };
    }
  }

  vi.stubGlobal('AudioContext', FakeAudioContext);
  return { voices, contexts, NOW };
}

function Probe() {
  const fire = useAlarm();
  return (
    <button type="button" onClick={fire}>
      fire
    </button>
  );
}

// fireEvent, not user-event: half these cases run on fake timers, and
// user-event's internal waits deadlock against them.
function fire(): void {
  render(<Probe />);
  fireEvent.click(screen.getByRole('button', { name: 'fire' }));
}

describe('useAlarm', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('strikes three rising notes, three partials each', async () => {
    const { voices, NOW } = fakeAudio();
    await fire();

    expect(voices).toHaveLength(9);
    expect(voices.every((voice) => voice.type === 'sine')).toBe(true);

    // Each note is a fundamental plus two partials, the second inharmonic —
    // that ratio is the whole reason it reads as a bell and not an organ.
    const first = voices.slice(0, 3).map((voice) => Math.round(voice.frequency));
    expect(first).toEqual([880, 1760, 2429]);

    // Rising, and struck in sequence rather than as one chord.
    const onsets = [voices[0], voices[3], voices[6]].map((voice) => voice?.start ?? 0);
    expect(onsets[0]).toBeCloseTo(NOW, 5);
    expect(onsets[1]).toBeGreaterThan(onsets[0] ?? 0);
    expect(onsets[2]).toBeGreaterThan(onsets[1] ?? 0);
    expect(voices[6]?.frequency).toBeGreaterThan(voices[0]?.frequency ?? 0);
  });

  it('never ramps to zero, which a real browser throws on', async () => {
    const { voices } = fakeAudio();
    await fire();

    for (const voice of voices) {
      expect(voice.ramps.length).toBeGreaterThan(0);
      for (const ramp of voice.ramps) expect(ramp.value).toBeGreaterThan(0);
      // Struck, then decaying: the loudest instant is the attack, and the last
      // ramp takes it back to the floor rather than cutting off at volume.
      const peak = Math.max(...voice.ramps.map((ramp) => ramp.value));
      expect(voice.ramps[voice.ramps.length - 1]?.value).toBeLessThan(peak);
    }
  });

  it('is over in about two seconds', async () => {
    const { voices, NOW } = fakeAudio();
    await fire();

    const end = Math.max(...voices.map((voice) => voice.stop));
    expect(end - NOW).toBeGreaterThan(1.5); // long enough to ring
    expect(end - NOW).toBeLessThan(2.5); // short enough not to talk over anyone
  });

  it('resumes a suspended context and closes it once the tail is done', async () => {
    vi.useFakeTimers();
    const { contexts } = fakeAudio();
    await fire();

    // A timer finishing is not a user gesture, so the context is born
    // suspended by the autoplay policy and stays silent without this.
    expect(contexts[0]?.resumed).toBe(true);
    expect(contexts[0]?.closed).toBe(false);

    // Not closed early — that would cut the ring off — but closed eventually,
    // or a long retro leaks one context per timer until audio stops working.
    await vi.advanceTimersByTimeAsync(1000);
    expect(contexts[0]?.closed).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(contexts[0]?.closed).toBe(true);
  });

  it('stays silent when the system asks for reduced motion', async () => {
    const { voices, contexts } = fakeAudio();
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList);

    await fire();

    // Not just quiet — nothing is constructed at all. Someone who asked the
    // system to calm down does not want a noise either.
    expect(voices).toHaveLength(0);
    expect(contexts).toHaveLength(0);
  });

  it('does nothing when the browser has no Web Audio at all', async () => {
    vi.stubGlobal('AudioContext', undefined);
    expect(() => fire()).not.toThrow();
  });
});
