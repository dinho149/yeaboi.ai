/**
 * Telling a screen reader that the page changed.
 *
 * A client-side router replaces the document's contents without a load, so
 * three things a browser normally does for free stop happening: the title never
 * changes, nothing is announced, and focus stays wherever it was — usually on
 * the link that was just clicked, which no longer exists. The result is that a
 * screen-reader user activates a link and is told nothing at all.
 *
 * The fix is the well-worn one: set the title, announce the new page name in a
 * polite live region, and move focus to the main region.
 *
 * Deliberately skipped on first render. The initial page load already announces
 * itself the ordinary way, and stealing focus into `<main>` on arrival would
 * skip a keyboard user past the navigation before they had reached it.
 */

import { useEffect, useRef, useState } from 'preact/hooks';

import { documentTitle, pageName } from './routeTitle';

export function useRouteAnnounce(pattern: string | null, path: string, mainRef: { current: HTMLElement | null }) {
  const [announcement, setAnnouncement] = useState('');
  const first = useRef(true);

  useEffect(() => {
    document.title = documentTitle(pattern, path);
    if (first.current) {
      first.current = false;
      return;
    }
    setAnnouncement(pageName(pattern, path));
    // tabIndex -1 makes a non-interactive region focusable without putting it
    // in the tab order; App.tsx sets it on <main>.
    mainRef.current?.focus();
  }, [pattern, path, mainRef]);

  return announcement;
}
