/**
 * The liveness layer.
 *
 * The headline test here is `enforces the animate-from rule`. It guards a trap
 * that is completely invisible in normal use: tokens.css flattens every
 * animation to 0.01ms under `prefers-reduced-motion`, so an enter animation
 * written as `to { opacity: 1 }` leaves the element at its *start* state
 * forever — the card animates in for everyone except the people who asked for
 * less motion, for whom it never appears at all. Nobody would catch that by
 * looking at the board.
 */

import { act, render, renderHook, screen } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import motionCss from './motion.module.css?raw';
import { Ticker } from './Ticker';
import { ARRIVAL_MS, useArrivals } from './useArrivals';

/** Every `@keyframes NAME { … }` block in the stylesheet, by name. */
function keyframes(css: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /@keyframes\s+([\w-]+)\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g;
  for (let m = re.exec(css); m !== null; m = re.exec(css)) {
    out[m[1] as string] = m[2] as string;
  }
  return out;
}

describe('motion.module.css', () => {
  const blocks = keyframes(motionCss);

  // `leave` is the documented exception: the element is being removed, so
  // there is no resting appearance to preserve and `to` is correct.
  const EXIT_ANIMATIONS = new Set(['leave']);
  // Looping ambient animations return to their own start every cycle, so
  // percentage stops are fine and flattening them just stops the loop.
  const LOOPS = new Set(['ghost-breathe']);

  it('found the keyframes to check', () => {
    // Guards against the regex silently matching nothing and the suite below
    // passing vacuously.
    expect(Object.keys(blocks).length).toBeGreaterThanOrEqual(5);
  });

  it.each(Object.keys(keyframes(motionCss)))('%s obeys the animate-from rule', (name) => {
    if (EXIT_ANIMATIONS.has(name) || LOOPS.has(name)) return;
    const body = blocks[name] as string;
    expect(body, `${name} should declare a "from" start state`).toMatch(/(^|\})\s*(from|0%)\s*\{/);
    expect(
      body,
      `${name} uses a "to"/100% block — under prefers-reduced-motion the end state is never reached, ` +
        'so the resting appearance must be the plain CSS default instead'
    ).not.toMatch(/(^|\})\s*(to|100%)\s*\{/);
  });
});

/**
 * The same trap, checked across every stylesheet rather than this one.
 *
 * The block above is strict, and it can afford to be because this module owns
 * nothing but entrances. Elsewhere the keyframes are a mix — ambient loops that
 * return to their own start, a confetti float that ends invisible on purpose, a
 * recording pulse — and holding all of them to "never write a `to` block" would
 * need an exemption list longer than the rule, which is how a guard stops
 * meaning anything.
 *
 * So this checks the narrow thing that is always a bug: **an element must not
 * depend on reaching the end of an animation to become visible.** Flattening to
 * 0.01ms is exactly what makes that fail, and it fails only for the people who
 * asked for less motion, on whose machines nobody is looking.
 */
describe('every animated stylesheet', () => {
  const sheets = import.meta.glob<string>('../**/*.module.css', { query: '?raw', import: 'default', eager: true });
  const animated = Object.entries(sheets).filter(([, css]) => css.includes('@keyframes'));

  it('found the stylesheets to check', () => {
    // A glob that silently matches nothing would let every case below pass.
    expect(animated.length).toBeGreaterThanOrEqual(4);
  });

  it.each(animated)('%s never restores visibility in a "to" block', (_path, css) => {
    for (const [name, body] of Object.entries(keyframes(css))) {
      const tail = /(?:^|\})\s*(?:to|100%)\s*\{([^}]*)\}/.exec(body);
      if (!tail) continue;
      expect(
        tail[1],
        `@keyframes ${name} reaches full opacity only at its end — under prefers-reduced-motion ` +
          'that frame never arrives and the element stays invisible. Put the visible state in the ' +
          'plain CSS and animate *from* the hidden one.'
      ).not.toMatch(/opacity\s*:\s*1\b|visibility\s*:\s*visible/);
    }
  });
});

describe('useArrivals', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('reports nothing for the snapshot the board opened with', () => {
    // Eleven cards flying in on load is a loading screen, not a signal.
    const { result } = renderHook(({ ids }) => useArrivals(ids), {
      initialProps: { ids: ['a', 'b', 'c'] },
    });
    expect([...result.current]).toEqual([]);
  });

  it('reports an id that appears later', () => {
    const { result, rerender } = renderHook(({ ids }) => useArrivals(ids), {
      initialProps: { ids: ['a'] },
    });
    rerender({ ids: ['a', 'b'] });
    expect([...result.current]).toEqual(['b']);
  });

  it('holds an arrival across the re-renders a long-poll causes', () => {
    // The bug this exists to prevent: the board re-renders several times a
    // second, so a plain "new since last render" diff strips the class one
    // frame after adding it and the animation is cut off mid-flight.
    const { result, rerender } = renderHook(({ ids }) => useArrivals(ids), {
      initialProps: { ids: ['a'] },
    });
    rerender({ ids: ['a', 'b'] });
    expect(result.current.has('b')).toBe(true);

    for (let i = 0; i < 5; i += 1) rerender({ ids: ['a', 'b'] });
    expect(result.current.has('b'), 'a poll re-render must not cancel the animation').toBe(true);

    act(() => void vi.advanceTimersByTime(ARRIVAL_MS + 10));
    expect(result.current.has('b')).toBe(false);
  });

  it('ignores ids the viewer caused themselves', () => {
    // Your own card must appear instantly; animating it makes your own typing
    // feel laggy.
    const mine = new Set(['mine']);
    const { result, rerender } = renderHook(({ ids }) => useArrivals(ids, (id) => !mine.has(id)), {
      initialProps: { ids: ['a'] },
    });
    rerender({ ids: ['a', 'mine', 'theirs'] });
    expect([...result.current]).toEqual(['theirs']);
  });

  it('does not re-announce an id it already skipped', () => {
    // Skipped ids still have to be recorded as seen, or the next render would
    // treat them as brand new.
    let skip = true;
    const { result, rerender } = renderHook(({ ids }) => useArrivals(ids, () => !skip), {
      initialProps: { ids: ['a'] },
    });
    rerender({ ids: ['a', 'b'] });
    expect([...result.current]).toEqual([]);
    skip = false;
    rerender({ ids: ['a', 'b', 'c'] });
    expect([...result.current]).toEqual(['c']);
  });

  it('announces a card that was deleted and came back', () => {
    const { result, rerender } = renderHook(({ ids }) => useArrivals(ids), {
      initialProps: { ids: ['a', 'b'] },
    });
    rerender({ ids: ['a'] });
    rerender({ ids: ['a', 'b'] });
    expect([...result.current]).toEqual(['b']);
  });
});

describe('<Ticker>', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('renders the value', () => {
    render(<Ticker value={4} label="cards" />);
    expect(screen.getByLabelText('cards').textContent).toBe('4');
  });

  // Substring matching is wrong here: the resting class is `ticker`, which
  // contains `tick`, so `toContain` passes whether or not it is animating.
  const isTicking = (container: Element): boolean =>
    container.querySelector('span')?.classList.contains('tick') ?? false;

  it('does not animate on first paint', () => {
    const { container } = render(<Ticker value={4} />);
    expect(isTicking(container)).toBe(false);
  });

  it('animates when the value changes, then settles', () => {
    const { container, rerender } = render(<Ticker value={4} />);
    rerender(<Ticker value={5} />);
    expect(isTicking(container)).toBe(true);
    act(() => void vi.advanceTimersByTime(400));
    expect(isTicking(container)).toBe(false);
  });
});
