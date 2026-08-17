/**
 * Stepping back through previous retros.
 *
 * The list is fetched once, on the first step back rather than at boot: most
 * boards are opened to run today's retro and never look behind them, and a page
 * that queries the store on load pays for a thing nobody asked for.
 *
 * A past retro is *not* board state. It has no revision, nothing polls it, and
 * nothing can be written to it — so it lives here rather than in the board
 * store, which exists to be replaced wholesale by the next poll.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { apiUrl, type Session } from '../runtime/api';
import type { RetroCard } from '../types/board';

/** One row of the history list — enough to label a step, not to draw a board. */
export interface RetroRun {
  id: number;
  run_at: string;
  retro_date: string;
  project_name: string;
  sprint_name?: string;
  card_count: number;
}

/** A finished retro, in the same card shape the live board renders. */
export interface PastRetro {
  date: string;
  sprint_name: string;
  project_name: string;
  participants: readonly string[];
  cards: readonly RetroCard[];
  carried: readonly RetroCard[];
}

export interface History {
  /** Previous retros, newest first. Empty until the first step back. */
  runs: readonly RetroRun[];
  /** How many steps back from today. 0 is the live board. */
  at: number;
  /** The retro being shown, or null when that is today's. */
  showing: PastRetro | null;
  /** True while a step is still loading, so the bar can say so. */
  loading: boolean;
  /** Move `delta` steps back (positive) or forward. Clamped at both ends. */
  step(delta: number): void;
  /** Back to the live board. */
  reset(): void;
}

async function get<T>(session: Session, extra: Record<string, string> = {}): Promise<T | null> {
  try {
    const response = await fetch(apiUrl(session, '/api/history', extra), { cache: 'no-store' });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export function useHistory(session: Session): History {
  const [runs, setRuns] = useState<readonly RetroRun[]>([]);
  const [at, setAt] = useState(0);
  const [showing, setShowing] = useState<PastRetro | null>(null);
  const [loading, setLoading] = useState(false);
  // The step the last request was for. A reply for a step you have already
  // moved off is stale, and applying it would drag the board back.
  const wanted = useRef(0);
  // Requested, and arrived. Two things, because the clamp below must not fire
  // between them: at that moment the list is empty and every step back looks
  // like a step past the end, so the first click would snap straight home.
  const asked = useRef(false);
  const [arrived, setArrived] = useState(false);

  const step = useCallback(
    (delta: number) => {
      setAt((current) => Math.max(0, current + delta));
      if (!asked.current) {
        asked.current = true;
        void get<{ retros: RetroRun[] }>(session).then((data) => {
          setRuns(data?.retros ?? []);
          setArrived(true);
        });
      }
    },
    [session]
  );

  const reset = useCallback(() => setAt(0), []);

  useEffect(() => {
    wanted.current = at;
    if (at === 0) {
      setShowing(null);
      setLoading(false);
      return;
    }
    const run = runs[at - 1];
    if (!run) {
      // Stepped past the end, or the list has not landed yet. Holding position
      // rather than snapping to the live board: the list is one request away and
      // the arrow disables itself the moment it arrives.
      setLoading(runs.length === 0);
      return;
    }
    setLoading(true);
    void get<{ retro: PastRetro }>(session, { id: String(run.id) }).then((data) => {
      if (wanted.current !== at) return;
      setShowing(data?.retro ?? null);
      setLoading(false);
    });
  }, [at, runs, session]);

  // A list that came back shorter than where you are standing.
  useEffect(() => {
    if (arrived && at > runs.length) setAt(runs.length);
  }, [arrived, at, runs.length]);

  return { runs, at, showing, loading, step, reset };
}
