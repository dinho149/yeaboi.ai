/**
 * The duck's state machine.
 *
 * Two properties matter more than the animation itself, because both are
 * silent when broken:
 *
 * 1. A meaningful resting state (offline, locked) is never masked by a
 *    decorative pulse. If a flap could hide "the connection is dead", the
 *    indicator is worse than nothing.
 * 2. Those states are expressed as plain CSS properties rather than keyframes,
 *    so they survive the global reduced-motion guard in tokens.css. A state
 *    encoded as an animation is invisible to the people who most need a
 *    non-moving cue.
 */

import { act, render, renderHook } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import duckCss from './duck.module.css?raw';
import { Duck, useDuckIdle, useDuckPulse, type DuckPulse, type DuckRest, type DuckState } from './Duck';

describe('<Duck>', () => {
  it('is hidden from assistive tech', () => {
    // Everything it signals is announced elsewhere: the lock banner is a
    // role="alert", the reconnect notice is in the toolbar subtitle, the timer
    // readout is aria-live. A label here would double-announce all of it.
    const { container } = render(<Duck />);
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true');
  });

  it('stacks all three layers so the rig can animate them separately', () => {
    const { container } = render(<Duck />);
    expect(container.querySelectorAll('img')).toHaveLength(3);
  });

  it.each(['offline', 'locked'] as const)('shows a literal sleep marker when %s', (state) => {
    // Not decoration — a keyframe would be flattened by the reduced-motion
    // guard, and this text is what remains for those visitors (and in a
    // screenshot, and on paper). Both resting states nap; colour is what tells
    // a dead connection from a closed room.
    const { container } = render(<Duck state={state} />);
    expect(container.textContent).toContain('z');
  });

  it('shows no sleep marker in any other state', () => {
    for (const state of ['idle', 'urgent', 'card'] as const) {
      const { container, unmount } = render(<Duck state={state} />);
      expect(container.textContent, `${state} should not read as asleep`).toBe('');
      unmount();
    }
  });
});

describe('the resting rig', () => {
  it('keeps the continuous motion off the node that owns arrival', () => {
    // One element cannot run an entrance and a loop at once — whichever is
    // declared second silently wins. The body layer is what makes both possible,
    // so its absence is a real regression and not a markup detail.
    const { container } = render(<Duck />);
    const body = container.querySelector('div > div');
    expect(body, 'the duck lost its body layer').toBeTruthy();
    expect(body?.querySelectorAll('img')).toHaveLength(3);
  });

  it('waddles in on every mount, and asks its caller for nothing', () => {
    // A bare `<Duck />` arrives — an export opened from disk gets the entrance
    // with no hook and no boot payload, same as a live board.
    const { container } = render(<Duck />);
    expect(container.querySelector('[data-enter="true"]')).toBeTruthy();
  });

  it('holds the entrance on the mount attribute, not the base rule', () => {
    // On the base rule, the states that cancel it to hold a static transform
    // re-arm it on the way out — unlocking a board replayed the whole waddle
    // instead of the duck turning back around.
    const base = /\.duck\s*\{([^}]*)\}/.exec(duckCss)?.[1] ?? '';
    expect(base).not.toMatch(/animation\s*:\s*duck-waddle-in/);
    expect(duckCss).toMatch(/\.duck\[data-enter=['"]true['"]\]\s*\{[^}]*duck-waddle-in/);
  });

  it('does not re-arm the entrance when a state is left', () => {
    // Leaving `locked` is a transform change on a node running nothing.
    vi.useFakeTimers();
    try {
      const { container, rerender } = render(<Duck state="locked" />);
      act(() => void vi.advanceTimersByTime(2000));
      rerender(<Duck state="idle" />);
      expect(container.querySelector('[data-enter="true"]')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(['locked', 'offline'])('cancels the entrance for %s', (state) => {
    // Without this the static transform these states rely on is overridden for
    // the first 1.4s — the exact window someone opening a dead board is looking.
    const block = new RegExp(`\\.duck\\[data-state="${state}"\\]\\s*\\{([^}]*)\\}`).exec(duckCss);
    expect(block?.[1]).toMatch(/animation\s*:\s*none/);
  });

  it('escalates the urgent flap past the resting one', () => {
    // Both are wing rotations; if the resting amplitude is raised to meet the
    // hard one, "ten seconds left" stops being visible as a change.
    const peakRotation = (name: string): number => {
      // Slice the block by hand: a keyframes body contains nested braces, so
      // there is no `[^}]*` that reaches the second percentage stop.
      const start = duckCss.indexOf(`@keyframes ${name} {`);
      expect(start, `no @keyframes ${name}`).toBeGreaterThan(-1);
      const body = duckCss.slice(start, duckCss.indexOf('\n}', start));
      const degrees = [...body.matchAll(/rotate\((-?[\d.]+)deg\)/g)].map((m) => Math.abs(Number(m[1])));
      return Math.max(...degrees);
    };
    expect(peakRotation('wing-flap-hard')).toBeGreaterThan(peakRotation('wing-flap') * 1.5);
  });
});

describe('useDuckIdle', () => {
  // The gap between mannerisms is randomised on purpose (a fixed one reads as a
  // GIF loop), which makes every timing here ambiguous unless the randomness is
  // pinned. With random() at 0 the wait is the 6s floor and the pick is the
  // first mannerism, so "advance past 6s" means exactly one thing.
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('plays a mannerism after a while, then clears it', () => {
    const { result } = renderHook(() => useDuckIdle(true));
    expect(result.current).toBeNull();

    act(() => void vi.advanceTimersByTime(6_100));
    expect(result.current).toBe('tilt');

    act(() => void vi.advanceTimersByTime(1_500));
    expect(result.current).toBeNull();
  });

  it('keeps cycling, so a board left open all afternoon stays alive', () => {
    // The failure this catches is a hook that fires once and then sits still —
    // which looks correct in the first ten seconds of a manual check.
    const { result } = renderHook(() => useDuckIdle(true));

    act(() => void vi.advanceTimersByTime(6_100));
    expect(result.current).toBe('tilt');
    act(() => void vi.advanceTimersByTime(1_500));
    expect(result.current).toBeNull();

    act(() => void vi.advanceTimersByTime(6_100));
    expect(result.current).toBe('tilt');
  });

  it('does nothing while the duck has something real to report', () => {
    // The second lock on the same door as the CSS ordering: a mannerism during a
    // reconnect would be motion that means nothing competing with motion that does.
    const { result } = renderHook(() => useDuckIdle(false));
    act(() => void vi.advanceTimersByTime(60_000));
    expect(result.current).toBeNull();
  });

  it('drops a running mannerism the moment it is disabled', () => {
    const { result, rerender } = renderHook<DuckIdleResult, { on: boolean }>(({ on }) => useDuckIdle(on), {
      initialProps: { on: true },
    });
    act(() => void vi.advanceTimersByTime(6_100));
    expect(result.current).toBe('tilt');

    rerender({ on: false });
    expect(result.current).toBeNull();
  });

  it('cancels its pending timer on unmount', () => {
    const clear = vi.spyOn(globalThis, 'clearTimeout');
    const { unmount } = renderHook(() => useDuckIdle(true));
    clear.mockClear();
    unmount();
    expect(clear).toHaveBeenCalled();
  });
});

type DuckIdleResult = ReturnType<typeof useDuckIdle>;

describe('duck.module.css', () => {
  // A regression guard on the rule stated in the module header. It is easy to
  // "tidy" one of these into a keyframe and never notice, because the only
  // people affected have prefers-reduced-motion set.
  it.each(['locked', 'offline'])('expresses %s as a plain transform, not an animation', (state) => {
    const block = new RegExp(`\\.duck\\[data-state="${state}"\\]\\s*\\{([^}]*)\\}`).exec(duckCss);
    expect(block, `no rule for data-state="${state}"`).toBeTruthy();
    const body = block?.[1] ?? '';
    expect(body).toMatch(/transform\s*:/);
    // The lookahead has to swallow the whitespace itself. Written as
    // `animation\s*:\s*(?!none)` the `\s*` backtracks to zero width, the
    // lookahead then sees " none", and the guard passes on the very thing it
    // exists to catch.
    expect(body, `${state} must not rely on a keyframe — the global guard kills it`).not.toMatch(
      /animation\s*:(?!\s*none\b)/
    );
  });
});

describe('useDuckPulse', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('plays a pulse and falls back to resting', () => {
    const { result } = renderHook(() => useDuckPulse('idle'));
    expect(result.current[0]).toBe('idle');

    act(() => result.current[1]('card'));
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current[0]).toBe('card');

    act(() => void vi.advanceTimersByTime(2000));
    expect(result.current[0]).toBe('idle');
  });

  it('replays the same pulse twice in a row', () => {
    // The reason the pulse is owned by the hook rather than passed in as a
    // prop: two cards arriving in a row would leave the prop at "card"
    // throughout, so React re-renders nothing and the animation never restarts.
    const { result } = renderHook(() => useDuckPulse('idle'));

    act(() => result.current[1]('card'));
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current[0]).toBe('card');

    act(() => result.current[1]('card'));
    expect(result.current[0]).toBe('idle'); // dropped, so the animation restarts
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current[0]).toBe('card');
  });

  it('never lets a decorative pulse mask a meaningful resting state', () => {
    // The failure this prevents: a card arrives just as the tunnel dies, the
    // duck flaps, and the board looks healthy while it is in fact stale.
    const { result, rerender } = renderHook<[DuckState, (p: DuckPulse) => void], { rest: DuckRest }>(
      ({ rest }) => useDuckPulse(rest),
      { initialProps: { rest: 'idle' } }
    );

    act(() => result.current[1]('card'));
    act(() => void vi.advanceTimersByTime(1));
    expect(result.current[0]).toBe('card');

    rerender({ rest: 'offline' });
    expect(result.current[0]).toBe('offline');
  });

  it('cancels its pending timer on unmount', () => {
    // A leaked timer here fires setPulse into a dead component. Preact only
    // warns, so asserting "does not throw" would pass even when it leaks —
    // count the cancellation instead.
    const clear = vi.spyOn(globalThis, 'clearTimeout');
    const { result, unmount } = renderHook(() => useDuckPulse('idle'));

    act(() => result.current[1]('startled'));
    act(() => void vi.advanceTimersByTime(1));
    clear.mockClear();

    unmount();
    expect(clear).toHaveBeenCalled();
    clear.mockRestore();
  });
});
