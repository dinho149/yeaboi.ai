/**
 * The masthead every yeaboi surface wears, in two variants.
 *
 * `document` — a terminal window (traffic lights, `yeaboi — <mode>`) over the
 * wordmark, title and a row of labelled facts, then the page, then a footer
 * with the duck and the credit. Exports and anything else that is a file.
 *
 * `app` — one screen for the live boards: a fixed, framed viewport holding a
 * floating chrome panel (compact masthead + toolbar) above the board. No
 * window, no footer, and the window itself never scrolls.
 *
 * Density picks the masthead's size: `hero` is the full ~260px header,
 * `compact` drops the six-row wordmark and the facts, `auto` reads the
 * viewport. `app` is always compact — its screen cannot spend 260px.
 */

import { Children, Fragment, isValidElement, useLayoutEffect, useRef, type ReactNode } from 'react';

import { Duck, Eyebrow, TerminalFrame, Wordmark } from '../design/primitives';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { cx } from '../runtime/cx';
import type { PageChrome } from './chrome';
import { Credit } from './Credit';
import { PopoverGroup } from './Popover';
import { useDockDrag, type DockEdge } from './useDockDrag';
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
  /** `document` grows and scrolls the window; `app` is one fixed screen. */
  variant?: 'document' | 'app';
  /**
   * `hero` is the full masthead, `compact` drops the six-row wordmark and the
   * facts, `auto` picks per viewport. `app` defaults to `compact`, `document`
   * to `hero`.
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
  /**
   * The tool cluster, docked bottom-right over the board. `app` only.
   *
   * Popovers inside it are mutually exclusive and should be given
   * `placement="above"` — a panel opening downward from the bottom edge is
   * off-screen with no way to scroll to it.
   */
  dock?: ReactNode;
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

/** A dock's controls, whether handed over as a fragment or as a list. */
function dockItems(dock: ReactNode): ReactNode[] {
  if (isValidElement(dock) && dock.type === Fragment) {
    return Children.toArray((dock.props as { children?: ReactNode }).children);
  }
  return Children.toArray(dock);
}

/**
 * One control, measured along the wall it is on.
 *
 * Its extent is its width lying down and its height on end, which is what lets
 * the split know how many controls have turned the corner.
 */
function DockItem({
  index,
  edge,
  onMeasure,
  children,
}: {
  index: number;
  edge: DockEdge;
  onMeasure: (index: number, extent: number) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    onMeasure(index, edge === 'bottom' ? box.width : box.height);
  });

  return (
    <span ref={ref} className={styles['dockItem']}>
      {children}
    </span>
  );
}

export function PageShell({
  chrome,
  variant = 'document',
  density = variant === 'app' ? 'compact' : 'hero',
  themeSwitcher,
  bar,
  dock,
  children,
  className,
  data,
}: PageShellProps) {
  // Subscribed unconditionally — hooks cannot be conditional — but only
  // consulted when density is 'auto'. The listener is one matchMedia per page.
  const roomy = useMediaQuery(HERO_VIEWPORT);
  // The controls, one by one: the dock splits across a corner, so it has to
  // know where each of them falls rather than laying out one row.
  const tools = dockItems(dock);
  const drag = useDockDrag(tools.length);
  const hero = density === 'auto' ? roomy : density === 'hero';

  const facts = chrome.facts ?? [];
  const badges = chrome.badges ?? [];
  const nav = chrome.nav ?? [];
  const app = variant === 'app';

  const body = (
    <>
      {/* An app surface's bar rides in the floating chrome below, not here. */}
      {app ? null : bar}

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

  // Documents only: a board spends every row of its screen on the board.
  const footer = app ? null : (
    <footer className={styles['footer']}>
      <Duck size={40} />
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
      {/* Divs, not <header>: a second unlabelled `banner` landmark fails axe. */}
      {app ? (
        <div className={styles['chromeApp']}>
          <div className={cx(styles['masthead'], styles['mastheadApp'])}>{head}</div>
          {bar}
        </div>
      ) : (
        <TerminalFrame title={chrome.frame} className={styles['masthead']}>
          {head}
        </TerminalFrame>
      )}

      {app ? (
        <div className={styles['appRegion']}>{body}</div>
      ) : (
        <>
          {body}
          {footer}
        </>
      )}

      {app && dock ? (
        <PopoverGroup>
          {(drag.runs.length ? drag.runs : [{ ...drag.placement, items: tools.map((_, i) => i) }]).map(
            (run, runIndex) => (
              <div
                key={run.edge + String(runIndex)}
                ref={runIndex === 0 ? drag.ref : undefined}
                className={cx(
                  styles['dockApp'],
                  drag.placed && styles['dockPlaced'],
                  drag.dragging && styles['dockDragging'],
                  drag.turning && styles['dockTurning'],
                )}
                data-edge={run.edge}
                style={
                  {
                    '--dock-x': `${run.x}px`,
                    '--dock-y': `${run.y}px`,
                    '--dock-vis': drag.runs.length ? 'visible' : 'hidden',
                  } as never
                }
                onPointerDown={drag.onPointerDown}
                role={runIndex === 0 ? 'toolbar' : 'group'}
                aria-label={runIndex === 0 ? 'Board tools' : 'Board tools, continued'}
              >
                {runIndex === 0 ? <span className={styles['dockGrip']} aria-hidden="true" /> : null}
                {run.items.map((item) => (
                  <DockItem key={item} index={item} edge={run.edge} onMeasure={drag.measure}>
                    {tools[item]}
                  </DockItem>
                ))}
              </div>
            ),
          )}
        </PopoverGroup>
      ) : null}
    </div>
  );
}
