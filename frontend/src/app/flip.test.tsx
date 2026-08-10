/**
 * FLIP: the shared-element transition, and the two assumptions it rests on.
 *
 * Both assumptions fail *silently* if they are wrong — the animation simply
 * does not happen, everything still renders, and nothing errors. That is the
 * only reason this file exists.
 *
 * 1. `flushSync` really does render synchronously. If Preact batched the swap,
 *    `Flip.from` would measure the old layout and produce a tween from a
 *    position to itself.
 * 2. Reduced motion produces no capture at all, so no tween is played.
 */

import { render } from '@testing-library/preact';
import { flushSync } from 'preact/compat';
import { useState } from 'preact/hooks';
import { describe, expect, it, vi } from 'vitest';

import { captureFlip, MOTION, playFlip } from './motion';

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

describe('flushSync', () => {
  it('renders synchronously, which is what makes the capture/play bracket work', () => {
    // If this ever becomes async, FLIP measures a layout that has not happened
    // and every shared-element transition quietly stops working.
    let bump = () => {};
    function Harness() {
      const [n, setN] = useState(0);
      bump = () => flushSync(() => setN((value) => value + 1));
      return <span data-testid="n">{n}</span>;
    }
    const { getByTestId } = render(<Harness />);
    expect(getByTestId('n').textContent).toBe('0');
    bump();
    // No await, no act: the DOM must already be updated on the next line.
    expect(getByTestId('n').textContent).toBe('1');
  });
});

describe('captureFlip', () => {
  it('captures nothing under reduced motion', () => {
    setReducedMotion(true);
    document.body.innerHTML = '<div data-flip-id="a"></div>';
    expect(captureFlip('[data-flip-id]')).toBeNull();
  });

  it('captures nothing when no element matches', () => {
    setReducedMotion(false);
    document.body.innerHTML = '<div></div>';
    expect(captureFlip('[data-flip-id]')).toBeNull();
  });

  it('captures a state when there is something to capture', () => {
    setReducedMotion(false);
    document.body.innerHTML = '<div data-flip-id="a"></div>';
    expect(captureFlip('[data-flip-id]')).not.toBeNull();
  });
});

describe('playFlip', () => {
  it('is a no-op for a null state, and its cleanup is safe', () => {
    expect(() => playFlip(null)()).not.toThrow();
  });

  it('returns a cleanup that can be called twice', () => {
    // A second navigation landing mid-flight must not leave an element
    // stranded between two layouts.
    setReducedMotion(false);
    document.body.innerHTML = '<div data-flip-id="a"></div>';
    const state = captureFlip('[data-flip-id]');
    const cleanup = playFlip(state);
    expect(() => {
      cleanup();
      cleanup();
    }).not.toThrow();
  });
});

describe('MOTION move tokens', () => {
  it('moves more slowly than it enters', () => {
    // The eye is tracking one thing across the screen rather than noticing
    // something appear; losing it defeats the point.
    expect(MOTION.move).toBeGreaterThan(MOTION.enter);
  });

  it('uses an in-out curve for a move and an out curve for an entrance', () => {
    expect(MOTION.moveEase).toContain('inOut');
    expect(MOTION.ease).not.toContain('inOut');
  });
});
