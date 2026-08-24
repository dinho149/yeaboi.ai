// The idle screensaver: a yard of ducks adrift over the app.
//
// The terminal's version composites half-block sprites at pixel precision
// because a terminal cell is the only canvas it has. Here the sprite is a real
// element, so this is a re-authoring rather than a port — same idea, same idle
// threshold (served with the ambience so the two cannot drift), same dismissal
// on any sign of a person.
//
// Under reduced motion the ducks stand still: the point is that the screen is
// idle, and that reads perfectly well without anything moving.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useMemo, useState } from 'react';

/** How many ducks. Enough to read as a yard, few enough to stay cheap. */
const DUCK_COUNT = 9;

export interface ScreensaverProps {
  /** Seconds of no input before it takes over. */
  idleSeconds: number;
  /** Held open by a keyboard shortcut rather than by idleness. */
  forced: boolean;
  onDismiss: () => void;
}

interface Floater {
  left: number;
  top: number;
  size: number;
  delay: number;
  duration: number;
}

/** Deterministic-enough scatter: computed once per mount, never per frame. */
function scatter(count: number): Floater[] {
  return Array.from({ length: count }, () => ({
    left: Math.random() * 88,
    top: Math.random() * 82,
    size: 48 + Math.random() * 56,
    delay: -Math.random() * 12,
    duration: 9 + Math.random() * 9,
  }));
}

export function Screensaver({ idleSeconds, forced, onDismiss }: ScreensaverProps) {
  const [idle, setIdle] = useState(false);
  const ducks = useMemo(() => scatter(DUCK_COUNT), []);

  useEffect(() => {
    let timer = 0;
    const reset = (): void => {
      window.clearTimeout(timer);
      setIdle(false);
      timer = window.setTimeout(() => setIdle(true), idleSeconds * 1000);
    };
    const events = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'] as const;
    for (const name of events) window.addEventListener(name, reset, { passive: true });
    reset();
    return () => {
      window.clearTimeout(timer);
      for (const name of events) window.removeEventListener(name, reset);
    };
  }, [idleSeconds]);

  const showing = forced || idle;
  useEffect(() => {
    if (!showing) return undefined;
    // Any sign of a person ends it, including the one that started it.
    const dismiss = (): void => {
      setIdle(false);
      onDismiss();
    };
    const events = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'] as const;
    for (const name of events) window.addEventListener(name, dismiss, { once: true, passive: true });
    return () => {
      for (const name of events) window.removeEventListener(name, dismiss);
    };
  }, [showing, onDismiss]);

  if (!showing) return null;

  return (
    <div class="screensaver" aria-hidden="true">
      {ducks.map((duck, index) => (
        <div
          key={index}
          class="screensaver-duck"
          style={{
            left: `${duck.left}%`,
            top: `${duck.top}%`,
            animationDelay: `${duck.delay}s`,
            animationDuration: `${duck.duration}s`,
          }}
        >
          <Duck state="idle" size={duck.size} />
        </div>
      ))}
    </div>
  );
}
