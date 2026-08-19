/**
 * Keep something mounted for the length of its exit.
 *
 * An element unmounted the moment its condition goes false has nothing left to
 * animate — it simply vanishes. This holds it for `ms` and says it is leaving,
 * so the same component can carry both halves.
 *
 * Layout, not effect: an ordinary effect runs after paint, so the render that
 * turned the condition off would show one frame with the element already gone
 * and the next with it back and folding.
 */

import { useLayoutEffect, useRef, useState } from 'react';

export interface Linger {
  /** Render it while this is true. */
  mounted: boolean;
  /** True only while it is on its way out. */
  leaving: boolean;
}

export function useLinger(open: boolean, ms: number): Linger {
  const [leaving, setLeaving] = useState(false);
  const wasOpen = useRef(open);

  useLayoutEffect(() => {
    const was = wasOpen.current;
    wasOpen.current = open;
    if (open || !was) return undefined;
    setLeaving(true);
    const timer = window.setTimeout(() => setLeaving(false), ms);
    return () => window.clearTimeout(timer);
  }, [open, ms]);

  return { mounted: open || leaving, leaving };
}
