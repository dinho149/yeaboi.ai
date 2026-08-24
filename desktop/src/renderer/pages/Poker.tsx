// Planning poker — the saved-sessions hub, and the way into a live table.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type BoardSnapshot, type PokerRun, loadBoards, pokerHistory } from '../boards';
import { ResultActions } from '../components/ResultActions';

export function Poker() {
  const [runs, setRuns] = useState<PokerRun[] | null>(null);
  const [live, setLive] = useState<BoardSnapshot | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    pokerHistory().then(
      (envelope) => setRuns(envelope.data?.history ?? []),
      (e: Error) => setError(e.message),
    );
    loadBoards().then(
      (body) => setLive(body.boards.find((board) => board.kind === 'poker') ?? null),
      () => undefined,
    );
  }, []);

  if (error && !runs) return <NoticeBlock title="Could not load past sessions" items={[error]} />;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Planning poker</h1>
          <p class="dash-sub">The team estimates from their own browsers; the points go back to the board.</p>
        </div>
        <div class="dash-actions">
          {live ? (
            <a class="button primary" href={`#/humans/poker/board?id=${encodeURIComponent(live.board_id)}`}>
              Rejoin the live table
            </a>
          ) : (
            <a class="button primary" href="#/humans/poker/new">
              New session
            </a>
          )}
        </div>
      </header>

      {!runs && <p>Loading…</p>}

      {runs && runs.length > 0 && (
        <div class="profile-list">
          {runs.map((run) => (
            <Card key={run.id} title={run.scope_label || run.poker_date}>
              <StatGrid>
                <StatTile label="Date" value={run.poker_date} />
                <StatTile label="Tickets" value={String(run.ticket_count ?? 0)} />
                <StatTile label="Estimated" value={String(run.estimated_count ?? 0)} />
              </StatGrid>
              {/* Export only. A poker session has no share document in any
                  surface — the estimates go back to the tracker instead. */}
              <ResultActions refer={{ kind: 'poker', session_id: run.session_id, run_id: run.id }} mode="poker" />
            </Card>
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <Card title="No sessions yet">
          <p>
            <Duck state="idle" size={28} /> Pick a sprint or the backlog, send the invite, and everyone votes at
            once — no anchoring on whoever spoke first.
          </p>
        </Card>
      )}
    </div>
  );
}
