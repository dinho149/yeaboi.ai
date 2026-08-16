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
 *
 * KNOWN GAP: a hard refresh while windowed still paints the top corners rounded,
 * and they stay until the next resize. Toggling fullscreen is correct in both
 * directions. So the mount-time reading disagrees with the post-resize one, and
 * which of the two signals below is at fault has not been established from real
 * numbers yet.
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
  const chrome = window.outerHeight - window.innerHeight;
  // jsdom defaults both to the same value, so the gap is 0 and this would report
  // fullscreen for every component test. Require the pair to be real first: a
  // headless environment has no window furniture to measure and should get the
  // square screen.
  if (!window.outerHeight || !window.innerHeight) return false;
  return chrome <= CHROME_SLACK_PX;
}

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

    // Now (mounted, so layout has happened), and again next frame — on a hard
    // refresh the first of these can still read a half-built window.
    apply();
    const frame = requestAnimationFrame(apply);

    // Three sources, because they are genuinely different transitions:
    // `fullscreenchange` is the Fullscreen API (the deck's `f` key); the
    // browser's own fullscreen and a bookmarks bar being toggled arrive as a
    // plain resize; and `load` is the one that catches a window still settling
    // after a refresh, which is the case this hook got wrong.
    window.addEventListener('resize', apply);
    window.addEventListener('load', apply);
    document.addEventListener('fullscreenchange', apply);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', apply);
      window.removeEventListener('load', apply);
      document.removeEventListener('fullscreenchange', apply);
    };
  }, []);

  return fills;
}

