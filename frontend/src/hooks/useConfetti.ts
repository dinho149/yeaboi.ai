/**
 * The timer-finished celebration: confetti on a canvas, themed.
 *
 * Ported from the identical copies in retro/page.py and poker/page.py, with two
 * changes:
 *
 * * The colours were six hardcoded hex literals in both files. They now come
 *   from `--confetti-1..6`, so the celebration matches whichever palette the
 *   room is looking at.
 * * It respects `prefers-reduced-motion`. A burst of 140 flying particles is
 *   exactly what that setting exists to suppress, and the CSS-level rule in
 *   tokens.css cannot reach a canvas animation.
 */

import { useCallback, useEffect, useRef } from 'react';

const PARTICLES = 140;
const FRAMES = 160;
const GRAVITY = 0.35;
const FADE = 0.008;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  colour: string;
  size: number;
  alpha: number;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Read the six themed confetti colours off the document. */
function palette(): string[] {
  const computed = getComputedStyle(document.documentElement);
  const colours = [1, 2, 3, 4, 5, 6]
    .map((n) => computed.getPropertyValue(`--confetti-${n}`).trim())
    .filter(Boolean);
  // A canvas cannot paint `var(--x)`, and a page that somehow lacks the tokens
  // should still celebrate rather than draw 140 invisible squares.
  return colours.length ? colours : ['#5ac88a', '#a371f7', '#ff5edb', '#4cc38a', '#ffcf5e', '#ff5e5e'];
}

/**
 * Returns `[ref, fire]` — attach the ref to a full-viewport `<canvas>` and call
 * `fire()` when the timer hits zero.
 */
export function useConfetti(): [ref: (el: HTMLCanvasElement | null) => void, fire: () => void] {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const raf = useRef<number | undefined>(undefined);

  const setRef = useCallback((el: HTMLCanvasElement | null) => {
    canvas.current = el;
  }, []);

  // Cancel on unmount: a board closed mid-burst would otherwise keep an rAF
  // loop alive against a detached canvas until the page navigates.
  useEffect(
    () => () => {
      if (raf.current !== undefined) cancelAnimationFrame(raf.current);
    },
    []
  );

  const fire = useCallback(() => {
    const el = canvas.current;
    if (!el || prefersReducedMotion()) return;
    const ctx = el.getContext('2d');
    if (!ctx) return;

    el.width = window.innerWidth;
    el.height = window.innerHeight;
    const colours = palette();
    const parts: Particle[] = Array.from({ length: PARTICLES }, (_, i) => ({
      x: window.innerWidth / 2,
      y: window.innerHeight / 3,
      vx: (Math.random() - 0.5) * 14,
      vy: Math.random() * -12 - 4,
      colour: colours[i % colours.length] as string,
      size: 3 + Math.random() * 4,
      alpha: 1,
    }));

    let frame = 0;
    if (raf.current !== undefined) cancelAnimationFrame(raf.current);

    const step = (): void => {
      ctx.clearRect(0, 0, el.width, el.height);
      frame += 1;
      for (const p of parts) {
        p.vy += GRAVITY;
        p.x += p.vx;
        p.y += p.vy;
        p.alpha -= FADE;
        ctx.globalAlpha = Math.max(0, p.alpha);
        ctx.fillStyle = p.colour;
        ctx.fillRect(p.x, p.y, p.size, p.size * 1.6);
      }
      ctx.globalAlpha = 1;
      if (frame < FRAMES) {
        raf.current = requestAnimationFrame(step);
      } else {
        raf.current = undefined;
        ctx.clearRect(0, 0, el.width, el.height);
      }
    };
    raf.current = requestAnimationFrame(step);
  }, []);

  return [setRef, fire];
}
