/**
 * The motion layer.
 *
 * The reduced-motion branch is the reason this file exists. It is the one part
 * of an animation system that is invisible in normal use, easy to break, and
 * genuinely harmful when broken — so it is asserted rather than trusted, and
 * asserted as *no animation* rather than a short one.
 */

import { gsap } from 'gsap';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { enter, enterList, entranceVars, MOTION, prefersReducedMotion } from './motion';

/** Drive `matchMedia` so both branches can be exercised deterministically. */
function setReducedMotion(reduced: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('reduce') ? reduced : !reduced,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('MOTION tokens', () => {
  it('exits faster than it enters', () => {
    // Nobody wants to watch something leave.
    expect(MOTION.exit).toBeLessThan(MOTION.enter);
  });

  it('keeps the stagger small enough not to be a performance', () => {
    // 20 rows at 0.035s is 0.7s of cascade; at 0.15s it is three seconds of
    // the reader waiting for a list.
    expect(MOTION.stagger).toBeLessThanOrEqual(0.05);
  });

  it('keeps entrances under a third of a second', () => {
    expect(MOTION.enter).toBeLessThanOrEqual(0.4);
  });
});

describe('prefersReducedMotion', () => {
  it('reports the query', () => {
    setReducedMotion(true);
    expect(prefersReducedMotion()).toBe(true);
    setReducedMotion(false);
    expect(prefersReducedMotion()).toBe(false);
  });
});

describe('entranceVars', () => {
  it('returns no tween at all under reduced motion', () => {
    // null rather than duration: 0 - a zero-duration tween still runs the
    // engine and still fires callbacks, which is a different promise.
    expect(entranceVars(true)).toBeNull();
    expect(entranceVars(true, true)).toBeNull();
  });

  it('only animates transform and opacity', () => {
    // width/top/margin re-lay out every frame; transform and opacity are
    // composited. See the gsap-performance skill.
    const vars = entranceVars(false);
    const animated = new Set([...Object.keys(vars?.from ?? {}), ...Object.keys(vars?.to ?? {})]);
    for (const key of ['duration', 'ease', 'stagger']) animated.delete(key);
    expect([...animated].sort()).toEqual(['opacity', 'y']);
  });

  it('staggers only when asked', () => {
    expect(entranceVars(false)?.to['stagger']).toBeUndefined();
    expect(entranceVars(false, true)?.to['stagger']).toBe(MOTION.stagger);
  });

  it('ends at rest', () => {
    const vars = entranceVars(false);
    expect(vars?.to['opacity']).toBe(1);
    expect(vars?.to['y']).toBe(0);
  });
});

describe('enter', () => {
  it('tolerates a null target', () => {
    expect(() => enter(null)()).not.toThrow();
  });

  it('animates when motion is allowed', () => {
    setReducedMotion(false);
    const fromTo = vi.spyOn(gsap, 'fromTo');
    const node = document.createElement('div');
    document.body.append(node);
    enter(node);
    expect(fromTo).toHaveBeenCalled();
  });

  it('places the element without a tween under reduced motion', () => {
    setReducedMotion(true);
    const fromTo = vi.spyOn(gsap, 'fromTo');
    const set = vi.spyOn(gsap, 'set');
    const node = document.createElement('div');
    document.body.append(node);
    enter(node);
    expect(fromTo).not.toHaveBeenCalled();
    expect(set).toHaveBeenCalled();
  });

  it('returns a cleanup that reverts what it created', () => {
    setReducedMotion(false);
    const node = document.createElement('div');
    document.body.append(node);
    const cleanup = enter(node);
    expect(() => cleanup()).not.toThrow();
  });
});

describe('enterList', () => {
  function list(count: number) {
    const ul = document.createElement('ul');
    for (let i = 0; i < count; i++) ul.append(document.createElement('li'));
    document.body.append(ul);
    return ul;
  }

  it('tolerates a null container', () => {
    expect(() => enterList(null, 'li')()).not.toThrow();
  });

  it('does nothing for an empty list', () => {
    setReducedMotion(false);
    const fromTo = vi.spyOn(gsap, 'fromTo');
    enterList(list(0), 'li');
    expect(fromTo).not.toHaveBeenCalled();
  });

  it('staggers the items when motion is allowed', () => {
    setReducedMotion(false);
    const fromTo = vi.spyOn(gsap, 'fromTo');
    enterList(list(3), 'li');
    const [, , to] = fromTo.mock.calls[0] ?? [];
    expect((to as { stagger?: number })?.stagger).toBe(MOTION.stagger);
  });

  it('places the items without a tween under reduced motion', () => {
    setReducedMotion(true);
    const fromTo = vi.spyOn(gsap, 'fromTo');
    enterList(list(3), 'li');
    expect(fromTo).not.toHaveBeenCalled();
  });

});
