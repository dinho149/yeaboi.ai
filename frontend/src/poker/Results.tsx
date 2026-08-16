/**
 * What the reveal produced: the spread, the median, and the AI's read.
 *
 * Placed between the ticket and the table rather than under the deck. While
 * voting there is nothing here and the table sits right below the ticket, where
 * it belongs; on reveal the deck is closed anyway, so the spread becomes the
 * page's focus instead of a below-the-fold footnote nobody scrolls to.
 *
 * The distribution is a bar per deck value, widest first by count, with the
 * modal value marked. That is the shape of the disagreement, which is the
 * question the room is about to discuss — a bare median hides exactly the
 * "three people said 2 and one said 21" case that a duel exists to resolve.
 */

import { useEffect, useState } from 'react';

import { Eyebrow } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { AiPerspective } from '../types/board';
import { fmtPoints } from './points';
import styles from './poker.module.css';

export interface ResultsProps {
  /** Deck-ordered `{value: count}`. Empty while voting. */
  distribution: Record<string, number>;
  median: number | null;
  suggestion: number | null;
  ai: AiPerspective;
  /** True once votes are public — `revealed` or `duel`. */
  revealed: boolean;
}

export function Results({ distribution, median, suggestion, ai, revealed }: ResultsProps) {
  const entries = Object.entries(distribution);
  const hasAi = ai.pending || Boolean(ai.note);
  const show = revealed || hasAi;

  // Asking for the perspective is a deliberate act, so it opens on the answer.
  // Once it is here, the tab is the reader's to move.
  const [tab, setTab] = useState<Tab>('spread');
  useEffect(() => setTab(hasAi ? 'ai' : 'spread'), [hasAi]);

  if (!show) return null;

  const max = entries.length ? Math.max(...entries.map(([, count]) => count)) : 0;
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  /* Null when the vote ties, so a split table is not drawn as two winners. */
  const mode = entries.filter(([, count]) => count === max).length === 1 ? max : null;
  const onAi = hasAi && tab === 'ai';

  return (
    <section className={styles['results']} aria-label="Results">
      <div className={styles['resultsHead']}>
        {/* One eyebrow until there are two things to show, then two tabs in its
            place — the AI's read is long enough to push the table off screen if
            it stacks under the spread. */}
        {hasAi ? (
          <div className={styles['rtabs']} role="tablist" aria-label="Results view">
            <Tabbed id="spread" tab={tab} onPick={setTab}>
              Results
            </Tabbed>
            <Tabbed id="ai" tab={tab} onPick={setTab}>
              AI perspective
            </Tabbed>
          </div>
        ) : (
          <Eyebrow>Results</Eyebrow>
        )}

        {onAi ? (
          ai.confidence ? (
            <span className={cx(styles['conf'], styles[`conf-${ai.confidence}`])}>{ai.confidence} confidence</span>
          ) : null
        ) : revealed && median !== null ? (
          <span className={styles['resultsSum']}>
            median {fmtPoints(median)}
            {suggestion !== null ? (
              <>
                {' '}
                → suggested <b>{fmtPoints(suggestion)}</b>
              </>
            ) : null}
          </span>
        ) : null}
      </div>

      {/* Both panels share one grid cell, so the box is always as tall as the
          taller of them and switching tabs cannot move the tabs. The one that
          is not showing keeps its space and loses its visibility, which is also
          what takes it out of the accessibility tree. */}
      <div className={styles['rpanel']}>
        <div
          role={hasAi ? 'tabpanel' : undefined}
          id="results-spread"
          aria-labelledby="results-tab-spread"
          className={cx(onAi && styles['rpanelOff'])}
        >
          {revealed && entries.length ? (
            <ul className={styles['dist']}>
              {entries.map(([value, count]) => (
                <li key={value} className={cx(styles['drow'], count === mode && styles['drowTop'])}>
                  <span className={styles['dval']}>{value}</span>
                  <span className={styles['dtrack']}>
                    {/* Share of the table, not of the tallest bar: normalising to
                        the max draws every tie as a full bar. */}
                    <span className={styles['dbar']} style={{ width: `${(count / total) * 100}%` }} />
                  </span>
                  <span className={styles['dcount']}>{count}</span>
                  {/* Pluralised on the total, not the count: "1 of 3 vote" is what
                      agreeing with `count` produces, and it is wrong. */}
                  <span className={styles['srOnly']}>
                    {count} of {total} {total === 1 ? 'vote' : 'votes'}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {hasAi ? (
          <div
            role="tabpanel"
            id="results-ai"
            aria-labelledby="results-tab-ai"
            className={cx(!onAi && styles['rpanelOff'])}
          >
            <AiNote ai={ai} />
          </div>
        ) : null}
      </div>
    </section>
  );
}

type Tab = 'spread' | 'ai';

function Tabbed({ id, tab, onPick, children }: { id: Tab; tab: Tab; onPick(next: Tab): void; children: string }) {
  return (
    <button
      type="button"
      role="tab"
      id={`results-tab-${id}`}
      aria-selected={tab === id}
      aria-controls={`results-${id}`}
      className={cx(styles['rtab'], tab === id && styles['rtabOn'])}
      onClick={() => onPick(id)}
    >
      {children}
    </button>
  );
}

function AiNote({ ai }: { ai: AiPerspective }) {
  if (ai.pending) {
    return (
      <div className={cx(styles['ainote'], styles['ainotePending'])} role="status">
        <p className={styles['aiBody']}>Thinking</p>
      </div>
    );
  }
  if (!ai.note) return null;
  // A fallback is the vote median in a sentence, and the decision row already
  // shows the median — so what is worth saying is why there is no perspective.
  if (!ai.from_llm) {
    return (
      <p className={styles['aiFallback']} role="status">
        {ai.note}
      </p>
    );
  }

  return <AiWritten ai={ai} />;
}

/** How long one word waits for the next. A read-along pace, not a wait. */
const WORD_MS = 22;

/**
 * The perspective arrives as one finished string, and lands a word at a time.
 *
 * The whole note is in the DOM from the first frame — the part not yet written
 * is transparent, not absent — so nothing reflows as it fills in and the tabs
 * above it do not move. It is also what a screen reader gets, whole, at once.
 */
function AiWritten({ ai }: { ai: AiPerspective }) {
  const note = ai.note;
  const [upto, setUpto] = useState(note.length);

  useEffect(() => {
    if (!note || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setUpto(note.length);
      return undefined;
    }
    setUpto(0);
    let at = 0;
    let timer = 0;
    const write = (): void => {
      const space = note.indexOf(' ', at + 1);
      at = space === -1 ? note.length : space;
      setUpto(at);
      if (at < note.length) timer = window.setTimeout(write, WORD_MS);
    };
    timer = window.setTimeout(write, WORD_MS);
    return () => window.clearTimeout(timer);
  }, [note]);

  const done = upto >= note.length;

  return (
    <div className={styles['ainote']}>
      <p className={styles['aiBody']}>
        {note.slice(0, upto)}
        <span className={styles['aiUnwritten']}>{note.slice(upto)}</span>
      </p>
      {/* The conclusion holds its place from the start and fades in once the
          reasoning above it is finished — reading the verdict first would give
          the argument away. */}
      {ai.evidence.length ? (
        <ul className={cx(styles['ev'], !done && styles['aiUnwritten'])}>
          {ai.evidence.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : null}
      {ai.suggested !== null ? (
        <p className={cx(styles['sug'], !done && styles['aiUnwritten'])}>
          AI suggests <b>{fmtPoints(ai.suggested)} points</b>
        </p>
      ) : null}
    </div>
  );
}
