/**
 * Which edges of a scroller have content past them.
 *
 * A mask that is always on fades the first card's top edge in a column holding
 * one card. The fade has to know whether there is anything to fade *to*, which
 * only the scroll position can say — so it is measured rather than assumed.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type Edges = 'none' | 'top' | 'bottom' | 'both';

export function useEdgeFade<T extends HTMLElement>(deps: unknown): [(el: T | null) => void, Edges] {
  const ref = useRef<T | null>(null);
  const [edges, setEdges] = useState<Edges>('none');

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // A pixel of slack: fractional scroll positions never land exactly on the end.
    const top = el.scrollTop > 1;
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
    setEdges(top && bottom ? 'both' : top ? 'top' : bottom ? 'bottom' : 'none');
  }, []);

  const attach = useCallback(
    (el: T | null) => {
      ref.current = el;
      measure();
    },
    [measure]
  );

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    measure();
    el.addEventListener('scroll', measure, { passive: true });
    // Absent in jsdom, and the scroll listener alone is still correct there.
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    observer?.observe(el);
    return () => {
      el.removeEventListener('scroll', measure);
      observer?.disconnect();
    };
    // `deps` is whatever changes the content's height — the card list.
  }, [measure, deps]);

  return [attach, edges];
}
