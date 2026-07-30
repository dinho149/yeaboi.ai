/**
 * The frame every exported report sits in.
 *
 * An export is the one surface where the terminal chrome is not a costume: the
 * file really was produced by a tool someone ran in a terminal, and the header
 * is that tool's mark. (The live boards deliberately do not get it — see
 * `TerminalFrame`.) So the masthead is a window: traffic lights, a title bar
 * reading `yeaboi — <mode>`, and inside it the block-glyph wordmark in the
 * report's own accent.
 *
 * The header carries **facts, not decoration**. Where the old shell had a row
 * of muted metadata strings, each one here is an eyebrow with a label — SOURCE,
 * ANALYZED, ENGINEER — because a report read six months later is exactly the
 * document where "what is this number" is the first question.
 *
 * The theme switcher is the five-swatch picker rather than the old cycle
 * button. Cycling made sense when the control was one `onclick` string in a
 * page with no runtime; with a real component there is no reason to make
 * someone click four times to get from midnight to forest. It prints as
 * nothing, along with the contents nav — both are controls, and a control on
 * paper is furniture that cost a reader ink.
 */

import { useState, type ReactNode } from 'react';

import { Duck, Eyebrow, TerminalFrame, Wordmark } from '../design/primitives';
import { setTheme, type Theme } from '../runtime/theme';
import { ThemeSwitcher } from '../shared';
import type { ExportChrome } from './boot';
import styles from './export.module.css';

export interface ShellProps {
  chrome: ExportChrome;
  /** The palette already applied to the document, before React mounted. */
  theme: Theme;
  children: ReactNode;
}

export function Shell({ chrome, theme: initial, children }: ShellProps) {
  const [theme, setThemeState] = useState<Theme>(initial);

  const choose = (next: Theme): void => {
    setThemeState(next);
    setTheme(next);
  };

  const facts = chrome.facts ?? [];
  const badges = chrome.badges ?? [];
  const nav = chrome.nav ?? [];

  return (
    <div className={styles['page']}>
      <TerminalFrame title={chrome.frame} className={styles['masthead']}>
        <div className={styles['head']}>
          <div className={styles['headMain']}>
            <Wordmark text={chrome.wordmark} className={styles['wordmark']} />
            <h1 className={styles['title']}>{chrome.title}</h1>
            {chrome.subtitle ? <p className={styles['subtitle']}>{chrome.subtitle}</p> : null}
            {facts.length || badges.length ? (
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
          <ThemeSwitcher value={theme} onChange={choose} className={styles['themes']} />
        </div>
      </TerminalFrame>

      {nav.length ? (
        <nav className={styles['toc']} aria-label="Contents">
          {nav.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </nav>
      ) : null}

      <main className={styles['container']}>{children}</main>

      <footer className={styles['footer']}>
        {/* The mascot, at rest. A duck that reacted to something would be
            lying: nothing on this page is live, and the states that matter on
            a board — a peer joining, the connection dropping — have no
            counterpart in a file. */}
        <Duck size={40} />
        {/* The credit is text, not a link, and deliberately so: the bundle must
            contain no external URL at all. `test_bundle_fetches_nothing` greps
            for one because it cannot tell an <a href> from a fetch in minified
            output, and a live report is worth more than a clickable byline.
            The Markdown twin, which is not under that constraint, links it. */}
        <span>{chrome.footer}</span>
      </footer>
    </div>
  );
}
