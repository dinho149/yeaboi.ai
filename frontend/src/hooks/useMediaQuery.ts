/**
 * Subscribe to a media query, as state.
 *
 * A CSS media query cannot change *which elements exist* — only how they look.
 * `PageShell` needs the former: below a large viewport the masthead swaps the
 * six-row wordmark for the two-row one and drops the facts row, and rendering
 * both and hiding one would put a 260px block in the DOM of every phone that
 * opens a board.
 *
 * `useSyncExternalStore` rather than `useEffect` + `useState`: it reads the
 * current value during render, so the first paint is already correct. The
 * effect version renders once at the wrong density and then corrects itself,
 * which on a board is a visible jump on every load.
 *
 * Both callbacks guard on `window.matchMedia` rather than assuming it. React's
 * third `getServerSnapshot` argument would be the tidier way to say that, but
 * these bundles alias React to preact/compat, whose `useSyncExternalStore`
 * takes two arguments — so the guard lives in the callbacks. jsdom is the case
 * that actually hits it: it has no `matchMedia`, and every component test that
 * renders a shell would otherwise throw.
 */

import { useCallback, useSyncExternalStore } from 'react';

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {};
      const list = window.matchMedia(query);
      // addEventListener, not addListener: the latter is deprecated, but Safari
      // below 14 has only the deprecated one and a tunnel link lands on
      // whatever phone the teammate owns. Fall back rather than throw.
      if (list.addEventListener) {
        list.addEventListener('change', onChange);
        return () => list.removeEventListener('change', onChange);
      }
      list.addListener(onChange);
      return () => list.removeListener(onChange);
    },
    [query],
  );

  const getSnapshot = useCallback(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  }, [query]);

  return useSyncExternalStore(subscribe, getSnapshot);
}
