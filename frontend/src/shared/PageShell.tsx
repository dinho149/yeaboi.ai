/**
 * The masthead and footer every yeaboi surface wears.
 *
 * A window: traffic lights, a title bar reading `yeaboi — <mode>`, and inside it
 * the wordmark in the surface's own accent, the title, and a row of facts. Then
 * the page. Then a footer with the duck and the credit.
 *
 * This began life inside `export/Shell.tsx`, dressing static reports only. The
 * boards, the gate and the deck each had their own header, and they drifted the
 * way five copies of anything drift — different wordmark sizes, some with a
 * footer and some without, one with no accent at all. It lives here now, and
 * they all compose it.
 *
 * The header carries **facts, not decoration**. Every entry is an eyebrow with
 * a label — SPRINT, SOURCE, ENGINEER — because a page read six months later is
 * exactly the document where "what is this number" is the first question.
 *
 * ## Two variants
 *
 * `document` is the original: a centred column that grows with its content and
 * scrolls the window. Exports and any other page that is fundamentally a file.
 *
 * `app` is for the live boards, which are `100dvh` and scroll *inside*. It lays
 * out a four-row grid — masthead / bar / content / footer — where only row 3
 * scrolls. `minmax(0, 1fr)` on that row is load-bearing: it is what keeps the
 * `min-height: 0` chain intact down to the board's own scroller, which would
 * otherwise refuse to shrink below its content and push the footer off-screen.
 *
 * ## Density
 *
 * The full masthead is around 260px — a hero on a desktop, and two thirds of a
 * phone. So `app` surfaces default to `density="auto"`, which drops to a
 * compact rendition below a large viewport: same window, same title, wordmark
 * in the two-row face instead of the six-row one, facts suppressed (a board's
 * facts already live in the toolbar subtitle).
 *
 * "Exactly like the export" holds wherever the viewport can pay for it. Compact
 * is the same masthead with the hero dropped — not a different header.
 */

import type { ReactNode } from 'react';

import { Duck, Eyebrow, TerminalFrame, Wordmark } from '../design/primitives';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { cx } from '../runtime/cx';
import type { PageChrome } from './chrome';
import styles from './PageShell.module.css';

/**
 * The viewport at which an `app` surface can afford the full masthead.
 *
 * Both dimensions, not just width: a landscape phone is wide enough and has no
 * vertical room at all, and the hero costs height rather than width. 1100/800
 * is the documented `--bp-m` paired with a height that clears a laptop.
 */
const HERO_VIEWPORT = '(min-width: 1100px) and (min-height: 800px)';

export interface PageShellProps {
  chrome: PageChrome;
  /** `document` grows and scrolls the window; `app` is a 100dvh grid. */
  variant?: 'document' | 'app';
  /**
   * `hero` is the full masthead, `compact` drops the six-row wordmark and the
   * facts. `auto` picks per viewport and is the default for `app`; documents
   * default to `hero`, because a file is read at whatever size it is read at
   * and has no fixed height to protect.
   */
  density?: 'hero' | 'compact' | 'auto';
  /**
   * The theme picker, as a node rather than a value/handler pair.
   *
   * Both boards already render `<ThemeSwitcher>` inside a popover with a host
   * "apply to everyone" control attached. If this component rendered its own,
   * every board would have two. Exports pass theirs; boards pass nothing.
   */
  themeSwitcher?: ReactNode;
  /** The app bar, on surfaces that have one. Sits between masthead and content. */
  bar?: ReactNode;
  /** The scrolling region. */
  children: ReactNode;
  className?: string | undefined;
  /**
   * Data attributes for the root element.
   *
   * Exists for one real case: poker's console is a fixed bottom sheet below
   * `--bp-m`, and several of its rules key off `[data-host]` on the layout root
   * to clear it. That flag has to reach the element this component owns, or the
   * selectors silently stop matching and the deck's last row hides behind the
   * host's bar on a phone.
   */
  data?: Record<string, string | undefined>;
}

export function PageShell({
  chrome,
  variant = 'document',
  density = variant === 'app' ? 'auto' : 'hero',
  themeSwitcher,
  bar,
  children,
  className,
  data,
}: PageShellProps) {
  // Subscribed unconditionally — hooks cannot be conditional — but only
  // consulted when density is 'auto'. The listener is one matchMedia per page.
  const roomy = useMediaQuery(HERO_VIEWPORT);
  const hero = density === 'auto' ? roomy : density === 'hero';

  const facts = chrome.facts ?? [];
  const badges = chrome.badges ?? [];
  const nav = chrome.nav ?? [];
  const app = variant === 'app';

  return (
    <div className={cx(styles['page'], app && styles['shellApp'], className)} {...data}>
      <TerminalFrame title={chrome.frame} className={styles['masthead']}>
        <div className={styles['head']}>
          <div className={styles['headMain']}>
            <Wordmark
              text={chrome.wordmark}
              variant={hero ? 'shadow' : 'block'}
              className={cx(styles['wordmark'], !hero && styles['wordmarkCompact'])}
            />
            <h1 className={cx(styles['title'], !hero && styles['titleCompact'])}>{chrome.title}</h1>
            {hero && chrome.subtitle ? <p className={styles['subtitle']}>{chrome.subtitle}</p> : null}
            {hero && (facts.length || badges.length) ? (
              <div className={styles['facts']}>
                {facts.map(([label, value]) => (
                  <Eyebrow key={label} value={value}>
                    {label}
                  </Eyebrow>
                ))}
                {badges.map((badge) => (
                  <Eyebrow key={badge} accent>
                    {badge}
                  </Eyebrow>
                ))}
              </div>
            ) : null}
          </div>
          {themeSwitcher ? <div className={styles['themes']}>{themeSwitcher}</div> : null}
        </div>
      </TerminalFrame>

      {bar}

      {nav.length ? (
        <nav className={styles['toc']} aria-label="Contents">
          {nav.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </nav>
      ) : null}

      <main className={cx(styles['container'], app && styles['containerApp'])}>{children}</main>

      <footer className={cx(styles['footer'], app && styles['footerSlim'])}>
        {/* The mascot, at rest. On an export a duck that reacted to something
            would be lying: nothing in a file is live. On a board the reactive
            duck already lives in the toolbar, and a second animated one in the
            footer would compete with it. */}
        <Duck size={app ? 24 : 40} />
        {/* The credit is text, not a link, and deliberately so: the bundle must
            contain no external URL at all. `test_bundle_fetches_nothing` greps
            for one because it cannot tell an <a href> from a fetch in minified
            output, and a live report is worth more than a clickable byline.
            The Markdown twin, which is not under that constraint, links it.
            Now that the boards wear this footer too, the rule binds retro.js
            and poker.js as well. */}
        <span>{chrome.footer}</span>
      </footer>
    </div>
  );
}
