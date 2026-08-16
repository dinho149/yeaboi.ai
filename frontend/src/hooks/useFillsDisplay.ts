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
 * "every case except this one". So this measures the thing that is actually being
 * decided: browser chrome occupies vertical space between the screen and the
 * viewport, and when it is gone that gap closes.
 *
 * Unlike the neighbouring `useMediaQuery` this does *not* read during render —
 * see `useFillsDisplay` below. A media query is authoritative the moment it is
 * asked; a window measurement is not.
 */

import { useEffect, useState } from 'react';

/**
 * Slack allowed between the window and its viewport before we call it "chrome
 * present".
 *
 * Not zero, because the two are not required to agree exactly, and not large,
 * because they must not be allowed to. A tab strip alone is ~35px and the
 * omnibox another ~40 before a bookmarks bar is even considered, so real chrome
 * clears this by an order of magnitude and there is no need to cut it fine in
 * either direction.
 */
const CHROME_SLACK_PX = 8;

/**
 * Measured against the window itself, deliberately.
 *
 * The first version of this compared `screen.height` to `innerHeight`, which is
 * the wrong pair: `screen` is the physical display, so display scaling, a second
 * monitor, or a window that is merely *maximised* all move that number
 * independently of the browser's furniture. On a scaled Retina display it never
 * closed, and the curve did not appear even in fullscreen.
 *
 * `outerHeight - innerHeight` is the chrome and nothing else — the window minus
 * what the page got. It is zero in fullscreen on any display, at any scale.
 */
export function fillsDisplay(): boolean {
  if (typeof window === 'undefined') return false;
  // The Fullscreen API is a direct answer where it applies (the deck's `f` key),
  // so take it before measuring anything.
  if (typeof document !== 'undefined' && document.fullscreenElement) return true;
  // jsdom defaults both to the same value, so the gap is 0 and this would report
  // fullscreen for every component test. Require the pair to be real first: a
  // headless environment has no window furniture to measure and should get the
  // square screen.
  if (!window.outerHeight || !window.innerHeight) return false;
  const chrome = window.outerHeight - window.innerHeight;
  // A viewport taller than the window it is inside is not a window with no
  // chrome, it is a reading that means nothing — page zoom scales `innerHeight`
  // and leaves `outerHeight` alone, so a zoomed-out page reports a negative gap
  // while sitting under a full tab strip. `<= slack` called that fullscreen.
  if (chrome < 0) return false;
  return chrome <= CHROME_SLACK_PX;
}

/**
 * When to look again after mounting, in ms.
 *
 * The first reading is the one that goes wrong, and until now nothing looked
 * again until the window was resized — so a board that read itself as fullscreen
 * on a hard refresh stayed curved for as long as you left it alone. These are
 * cheap (two integer reads and a `setState` that usually bails) and bounded, so
 * a bad first read costs a second rather than the session.
 */
const SETTLE_MS = [60, 250, 1000];

/**
 * Starts square, then corrects — deliberately, and not the way the neighbouring
 * `useMediaQuery` does it.
 *
 * `useSyncExternalStore` reads during render, which is right for a media query
 * and wrong here: on a hard refresh the window's metrics are not settled at first
 * render, the gap measured 0, and a windowed board painted its top corners
 * rounded and kept them until the next resize. Toggling fullscreen fixed it,
 * which is exactly the signature of a bad first read rather than bad logic.
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
