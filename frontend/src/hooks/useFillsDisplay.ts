/**
 * Whether the page owns the whole display, measured rather than asked.
 *
 * The board draws its screen as a rounded rectangle with `--screen-void` showing
 * through the corners. That only reads as a device edge when what sits behind it
 * is the bezel — with a tab strip, an omnibox and a bookmarks bar on screen the
 * same four notches land against the browser's own chrome, where they read as a
 * rendering fault.
 *
 * `(display-mode: fullscreen)` is the obvious way to ask, and it is not reliable
 * here: it stayed true in a plainly windowed Chrome, so the curve never came off.
 * `not all and (…)` is worse — long-standing hack territory that does not mean
 * "every case except this one". So this measures instead.
 *
 * Unlike the neighbouring `useMediaQuery` this does *not* read during render —
 * see `useFillsDisplay` below. A media query is authoritative the moment it is
 * asked; a window measurement is not.
 */

import { useEffect, useState } from 'react';

/**
 * How far down the display the viewport may start and still count as the top.
 *
 * Small, because the real gap is not close: the least chrome any browser window
 * has above its page is a tab strip, and on a Mac there is a menu bar above that
 * — well over a hundred pixels between the two answers.
 */
const CHROME_SLACK_PX = 8;

/**
 * One number, not a difference of two.
 *
 * `screenY` is where the page's own viewport starts on the display, and every
 * piece of the browser — menu bar, tab strip, omnibox, bookmarks — is above that
 * line. So it *is* the question, and it is a fact the window manager already
 * knows: nothing has to lay out or settle before it is true, and page zoom,
 * display scaling and a merely-maximised window all leave it alone.
 *
 * Two pairs were tried before it and both are wrong for the same reason — they
 * ask about sizes when the question is about position. `screen.height` against
 * `innerHeight` moves with display scaling and never closed on a Retina;
 * `outerHeight` against `innerHeight` is the chrome only while both are settled
 * and unzoomed, and a page zoomed out reports *less* than nothing.
 */
export function fillsDisplay(): boolean {
  if (typeof window === 'undefined') return false;
  // The Fullscreen API is a direct answer where it applies (the deck's `f` key),
  // so take it before measuring anything.
  if (typeof document !== 'undefined' && document.fullscreenElement) return true;
  // jsdom reports a screen with no height, and so does anything else with no
  // display behind it. There is nothing to fill.
  if (!window.screen?.height) return false;
  const above = window.screenY;
  // Below zero is a window on a display above the primary one — somewhere else,
  // not a page that owns its screen.
  if (!Number.isFinite(above) || above < 0) return false;
  return above <= CHROME_SLACK_PX;
}

/**
 * When to look again after mounting, in ms.
 *
 * A window that has just been restored out of fullscreen is still moving when
 * the first document of a reload runs, and nothing else is guaranteed to ask
 * again — a board that read itself wrong stayed that way until the next resize.
 * These are cheap (one property read and a `setState` that usually bails) and
 * bounded, so a bad first read costs a second rather than the session.
 */
const SETTLE_MS = [60, 250, 1000];

/**
 * Starts square, then corrects — deliberately, and not the way the neighbouring
 * `useMediaQuery` does it.
 *
 * `useSyncExternalStore` reads during render, which is right for a media query
 * and wrong here: a measurement of a window is only as good as the moment it is
 * taken, and the moment a document first renders is the worst one available.
 *
 * So the initial value is a flat `false` rather than a measurement. The two
 * possible mistakes are not equal: starting square and adding the curve a frame
 * later is invisible, while starting curved and removing it is the bug above,
 * and if the correction never runs the board is merely square. Measure only once
 * there is something real to measure.
 */
export function useFillsDisplay(): boolean {
  const [fills, setFills] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const apply = () => setFills(fillsDisplay());

    // Now (mounted, so layout has happened), again next frame, and then on a
    // short schedule — on a hard refresh the early readings can still be of a
    // half-built window, and nothing else is guaranteed to ask again.
    apply();
    const frame = requestAnimationFrame(apply);
    const timers = SETTLE_MS.map((ms) => window.setTimeout(apply, ms));

    // `fullscreenchange` is the Fullscreen API (the deck's `f` key); the
    // browser's own fullscreen and a bookmarks bar being toggled arrive as a
    // plain resize. `load` is only worth subscribing to if it has not already
    // fired — a listener added afterwards never runs, which is how the one case
    // this was added for went on failing.
    window.addEventListener('resize', apply);
    document.addEventListener('fullscreenchange', apply);
    if (document.readyState !== 'complete') window.addEventListener('load', apply);
    return () => {
      cancelAnimationFrame(frame);
      timers.forEach(window.clearTimeout);
      window.removeEventListener('resize', apply);
      window.removeEventListener('load', apply);
      document.removeEventListener('fullscreenchange', apply);
    };
  }, []);

  return fills;
}
