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
import { Duck, useDuckPulse, type DuckPulse, type DuckRest, type DuckState } from './Duck';

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

  it('shows a literal sleep marker when offline', () => {
    // Not decoration — a keyframe would be flattened by the reduced-motion
    // guard, and this text is what remains for those visitors (and in a
    // screenshot, and on paper).
    const { container } = render(<Duck state="offline" />);
    expect(container.textContent).toContain('z');
  });

  it('shows no sleep marker in any other state', () => {
    for (const state of ['idle', 'urgent', 'locked', 'card'] as const) {
      const { container, unmount } = render(<Duck state={state} />);
      expect(container.textContent, `${state} should not read as asleep`).toBe('');
      unmount();
    }
  });
});

describe('duck.module.css', () => {
  // A regression guard on the rule stated in the module header. It is easy to
  // "tidy" one of these into a keyframe and never notice, because the only
  // people affected have prefers-reduced-motion set.
  it.each(['locked', 'offline'])('expresses %s as a plain transform, not an animation', (state) => {
    const block = new RegExp(`\\.duck\\[data-state="${state}"\\]\\s*\\{([^}]*)\\}`).exec(duckCss);
    expect(block, `no rule for data-state="${state}"`).toBeTruthy();
    const body = block?.[1] ?? '';
    expect(body).toMatch(/transform\s*:/);
    expect(body, `${state} must not rely on a keyframe — the global guard kills it`).not.toMatch(
      /animation\s*:\s*(?!none)/
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
