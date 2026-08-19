/**
 * The one question this answers is "is anything of the browser above the page".
 *
 * It has been answered wrong twice with size arithmetic — `screen.height` against
 * `innerHeight`, then `outerHeight` against `innerHeight` — and both times the
 * failure was silent and only visible on somebody's real window. So the cases
 * that broke it are pinned here rather than left to a screenshot.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { fillsDisplay } from './useFillsDisplay';

/** jsdom's window is stubbable but not writable; put it back after each case. */
const originals: Array<() => void> = [];

function stub(target: object, key: string, value: unknown): void {
  const prior = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { value, configurable: true });
  originals.push(() => {
    if (prior) Object.defineProperty(target, key, prior);
    else delete (target as Record<string, unknown>)[key];
  });
}

function display(height: number, viewportTop: number): void {
  stub(window.screen, 'height', height);
  stub(window, 'screenY', viewportTop);
}

afterEach(() => {
  while (originals.length) originals.pop()?.();
});

describe('fillsDisplay', () => {
  it('is true when the viewport starts at the top of the display', () => {
    display(1080, 0);
    expect(fillsDisplay()).toBe(true);
  });

  it('is false under a menu bar and a tab strip', () => {
    display(1080, 155);
    expect(fillsDisplay()).toBe(false);
  });

  it('is false with only a tab strip, on a platform with no menu bar', () => {
    display(1080, 88);
    expect(fillsDisplay()).toBe(false);
  });

  it('is false on a display above the primary one', () => {
    // Chrome measures from the primary screen's origin, so this goes negative —
    // a window somewhere else, not a page that owns its screen.
    display(1080, -400);
    expect(fillsDisplay()).toBe(false);
  });

  it('is false where there is no display behind the page', () => {
    // jsdom's default, and the reason the old size arithmetic reported
    // fullscreen for every component test: its outer and inner heights match.
    display(0, 0);
    expect(fillsDisplay()).toBe(false);
  });

  it('does not care what the window and the viewport measure', () => {
    // Page zoom scales `innerHeight` and leaves `outerHeight` alone, which is
    // how a zoomed-out window under a full tab strip reported *less* than no
    // chrome at all. Position does not move with zoom.
    display(1080, 155);
    stub(window, 'outerHeight', 900);
    stub(window, 'innerHeight', 1200);
    expect(fillsDisplay()).toBe(false);
  });

  it('takes the Fullscreen API at its word before measuring', () => {
    display(1080, 155);
    stub(document, 'fullscreenElement', document.body);
    expect(fillsDisplay()).toBe(true);
  });
});
