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
import type { AiPerspective, DuelSlice } from '../types/board';
import { Panel, Panels, Tabs, Written } from '../shared/board';
import { Duel } from './Duel';
import { fmtPoints } from './points';
import styles from './poker.module.css';

export interface ResultsProps {
  /** Deck-ordered `{value: count}`. Empty while voting. */
  distribution: Record<string, number>;
  median: number | null;
  suggestion: number | null;
  ai: AiPerspective;
  /** The open floor, when there is one. Null the rest of the time. */
  duel: DuelSlice | null;
  /** Seconds left on the board timer, for the turn clock between the two. */
  remaining: number | null;
  /** The floor's own controls are host-only, and they live inside it. */
  isHost: boolean;
  onNextTurn(): void;
  onCloseDuel(): void;
  /** True once votes are public — `revealed` or `duel`. */
  revealed: boolean;
}

export function Results({
  distribution,
  median,
  suggestion,
  ai,
  duel,
  remaining,
  isHost,
  onNextTurn,
  onCloseDuel,
  revealed,
}: ResultsProps) {
  const entries = Object.entries(distribution);
  const hasAi = ai.pending || Boolean(ai.note);
  const hasFloor = Boolean(duel);
  const show = revealed || hasAi || hasFloor;

  // Each of these arrives because somebody asked for it, so it opens on the
  // answer. Once it is here, the tab is the reader's to move. The floor comes
  // last because it is the one that is live and on a clock.
  const [tab, setTab] = useState<Tab>('spread');
  useEffect(() => setTab(hasAi ? 'ai' : 'spread'), [hasAi]);
  useEffect(() => setTab(hasFloor ? 'floor' : 'spread'), [hasFloor]);

  if (!show) return null;

  const max = entries.length ? Math.max(...entries.map(([, count]) => count)) : 0;
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  /* Null when the vote ties, so a split table is not drawn as two winners. */
  const mode = entries.filter(([, count]) => count === max).length === 1 ? max : null;
  const tabbed = hasAi || hasFloor;
  const onAi = hasAi && tab === 'ai';
  const onFloor = hasFloor && tab === 'floor';
  const onSpread = !onAi && !onFloor;

  return (
    <section className={styles['results']} aria-label="Results">
      <div className={styles['resultsHead']}>
        {/* One eyebrow until there are two things to show, then two tabs in its
            place — the AI's read is long enough to push the table off screen if
            it stacks under the spread. */}
        {tabbed ? (
          <Tabs<Tab>
            label="Results view"
            current={tab}
            onPick={setTab}
            tabs={[
              { id: 'spread' as Tab, label: 'Results' },
              ...(hasAi ? [{ id: 'ai' as Tab, label: 'AI perspective' }] : []),
              ...(hasFloor ? [{ id: 'floor' as Tab, label: 'The floor' }] : []),
            ]}
          />
        ) : (
          <Eyebrow>Results</Eyebrow>
        )}

        {onFloor ? null : onAi ? (
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

      <Panels>
        <Panel id="spread" label="Results view" showing={onSpread} tabbed={tabbed}>
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
        </Panel>

        {hasAi ? (
          <Panel id="ai" label="Results view" showing={onAi}>
            <AiNote ai={ai} />
          </Panel>
        ) : null}

        {duel ? (
          <Panel id="floor" label="Results view" showing={onFloor}>
            <Duel
              duel={duel}
              remaining={remaining}
              isHost={isHost}
              onNextTurn={onNextTurn}
              onCloseDuel={onCloseDuel}
            />
          </Panel>
        ) : null}
      </Panels>
    </section>
  );
}

type Tab = 'spread' | 'ai' | 'floor';

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

  return (
    <div className={styles['ainote']}>
      <Written text={ai.note} className={styles['aiBody']}>
        {ai.evidence.length ? (
          <ul className={styles['ev']}>
            {ai.evidence.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        ) : null}
        {ai.suggested !== null ? (
          <p className={styles['sug']}>
            AI suggests <b>{fmtPoints(ai.suggested)} points</b>
          </p>
        ) : null}
      </Written>
    </div>
  );
}
