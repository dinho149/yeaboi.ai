/**
 * Reactions everyone in the room sees float up the screen.
 *
 * The board keeps a bounded ticker of recent reactions (25 entries) and every
 * snapshot carries it. A client tracks the highest id it has animated and plays
 * only what is newer, so two browsers watching the same board see the same
 * emoji at roughly the same moment.
 *
 * ## Two guards worth keeping
 *
 * **Seeding.** On the very first snapshot the high-water mark is set without
 * animating anything. Otherwise somebody joining a board with a lively backlog
 * gets twenty-five emoji launched in their face on arrival.
 *
 * **Reduced motion.** The whole overlay is skipped when the visitor asks for
 * reduced motion. Nothing is lost — the counts on the cards are the durable
 * record; this is the ephemeral flourish on top.
 */

import { useEffect, useRef, useState } from 'react';

import type { ReactionEvent } from '../types/board';
import styles from './retro.module.css';

/** Sprites per reaction, and the ceiling on how many may be alive at once. */
const PER_EVENT = 3;
const MAX_ALIVE = 60;

interface Floater {
  key: string;
  emoji: string;
  left: number;
  delay: number;
  size: number;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function FloatingEmoji({ events }: { events: readonly ReactionEvent[] }) {
  const [alive, setAlive] = useState<Floater[]>([]);
  const highWater = useRef(-1);
  const seeded = useRef(false);
  const nextKey = useRef(0);

  useEffect(() => {
    const highest = events.reduce((max, event) => Math.max(max, event.id), highWater.current);

    if (!seeded.current) {
      seeded.current = true;
      highWater.current = highest;
      return;
    }

    const fresh = events.filter((event) => event.id > highWater.current);
    highWater.current = highest;
    if (!fresh.length || prefersReducedMotion()) return;

    const born: Floater[] = [];
    for (const event of fresh) {
      for (let i = 0; i < PER_EVENT; i += 1) {
        nextKey.current += 1;
        born.push({
          key: `f${nextKey.current}`,
          emoji: event.emoji,
          left: 8 + Math.random() * 84,
          delay: Math.random() * 0.4,
          size: 26 + Math.random() * 18,
        });
      }
    }
    // Drop the oldest rather than refusing new ones: a burst should show the
    // most recent reactions, not the first ones to arrive during it.
    setAlive((current) => [...current, ...born].slice(-MAX_ALIVE));
  }, [events]);

  if (!alive.length) return null;

  return (
    <div className={styles['floatLayer']} aria-hidden="true">
      {alive.map((floater) => (
        <span
          key={floater.key}
          className={styles['floater']}
          style={{
            left: `${floater.left}vw`,
            animationDelay: `${floater.delay}s`,
            fontSize: `${floater.size}px`,
          }}
          // Removed on its own animationend rather than by a timer: a
          // backgrounded tab does not run animations, and a timer would clear
          // sprites that never actually played.
          onAnimationEnd={() => setAlive((current) => current.filter((f) => f.key !== floater.key))}
        >
          {floater.emoji}
        </span>
      ))}
    </div>
  );
}
