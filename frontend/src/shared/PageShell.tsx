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
 * `app` is for the live boards, and it is **one screen**: a fixed box inset
 * from the window edge, framed in a curved border the way the TUI frames a
 * terminal, holding the masthead strip, the sticky bar, the board and the
 * credit. The window itself never scrolls — anything that has to scroll does
 * it inside the board.
 *
 * It went through both alternatives first. A `100dvh` grid locked the masthead
 * and credit to the top and bottom edges and spent a fifth of the viewport on
 * a wordmark read once. Letting the masthead *scroll away* fixed that and
 * bought a worse problem: a board is the one surface a visitor stares at for
 * forty minutes, and one that answers a stray wheel-flick by sliding its own
 * identity off the top — or worse, sliding the deck half out of view — is a
 * board you have to keep re-aiming. So the masthead shrank instead of moving.
 *
 * ## Density
 *
 * The full masthead is around 260px — a hero on a desktop, and two thirds of a
 * phone. `app` surfaces are therefore always `compact`: the wordmark drops to
 * the two-row face and sits inline with the title on a single strip, and the
 * facts are suppressed (a board's facts already live in the toolbar subtitle).
 * There is no viewport that can pay 260px for a header on a page that must not
 * scroll, so this is a constant rather than the `auto` media query it was.
 *
 * `document` keeps the terminal window — traffic lights, `yeaboi — <mode>`
 * title bar — because on a file that chrome is branding. On a live board it
 * was a costume, and the primitive's own comment said so.
 */

import type { ReactNode } from 'react';

import { Duck, Eyebrow, TerminalFrame, Wordmark } from '../design/primitives';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { cx } from '../runtime/cx';
import type { PageChrome } from './chrome';
import { Credit } from './Credit';
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
   * facts. `auto` picks per viewport. `app` defaults to `compact` — its screen
   * does not scroll, so there is no viewport that can afford the hero — and
   * documents default to `hero`, because a file is read at whatever size it is
   * read at and has no fixed height to protect.
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
  density = variant === 'app' ? 'compact' : 'hero',
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

  const body = (
    <>
      {/* Sticky only on an app surface, and only because the masthead above it
          now leaves: the toolbar carries invite, theme, music and the timer,
          and a board whose controls scrolled off the top would trade one
          annoyance for a worse one. A document's contents nav does its own
          sticking, below. */}
      {app && bar ? <div className={styles['barSticky']}>{bar}</div> : bar}

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
    </>
  );

  const footer = (
    <footer className={cx(styles['footer'], app && styles['footerSlim'])}>
      {/* The mascot, at rest. On an export a duck that reacted to something
          would be lying: nothing in a file is live. On a board the reactive
          duck already lives in the toolbar, and a second animated one in the
          footer would compete with it. */}
      <Duck size={app ? 24 : 40} />
      <Credit>{chrome.footer}</Credit>
    </footer>
  );

  const head = (
    <div className={cx(styles['head'], app && styles['headApp'])}>
      <div className={cx(styles['headMain'], app && styles['headMainApp'])}>
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
  );

  return (
    <div className={cx(styles['page'], app && styles['shellApp'], className)} {...data}>
      {/* The board wears no window. `TerminalFrame`'s own comment reserves it
          for "gate and export headers only — never a live board, where it would
          be a costume", and the shell was the one caller ignoring that. What
          the board gets instead is the frame around the *whole screen*, drawn
          by `.shellApp` — the same device the TUI uses, at the size the TUI
          uses it. Two chrome layers, an inner window inside an outer one, is
          what made the first version read as a screenshot of an app rather
          than the app. */}
      {/* A div, not a <header>. `<header>` at the top level of a document is the
          `banner` landmark, and a board already has one from the document
          variant's own structure — two unlabelled banners is an axe
          `landmark-unique` violation, which a11y.test.tsx caught on the first
          attempt. The document variant's masthead is a div too (it is
          TerminalFrame's root), so this is parity rather than a concession. */}
      {app ? (
        <div className={cx(styles['masthead'], styles['mastheadApp'])}>{head}</div>
      ) : (
        <TerminalFrame title={chrome.frame} className={styles['masthead']}>
          {head}
        </TerminalFrame>
      )}

      {/* The credit joins the region on an app surface rather than trailing it.
          That is what makes the arithmetic come out: the document is then taller
          than the viewport by exactly the masthead, so scrolling to the end puts
          the bar at the top and the credit on the bottom edge with nothing
          hidden. Left outside, the last few pixels of scroll would slide the
          board's column headings under the sticky bar. */}
      {app ? (
        <div className={styles['appRegion']}>
          {body}
          {footer}
        </div>
      ) : (
        <>
          {body}
          {footer}
        </>
      )}
    </div>
  );
}
