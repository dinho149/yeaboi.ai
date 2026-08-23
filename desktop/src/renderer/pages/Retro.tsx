// Retro — the saved-runs hub, and the way into a live board.
//
// A live board rejoins rather than restarts: the session lives in the backend
// so a reloaded window walks back into the ceremony it left.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type BoardSnapshot, type RetroRun, loadBoards, retroHistory, startRetroBoard } from '../boards';
import { ResultActions } from '../components/ResultActions';

export function Retro() {
  const [runs, setRuns] = useState<RetroRun[] | null>(null);
  // The session the history belongs to is a sibling of the rows, not a column
  // on them — an artifact reference needs both halves.
  const [sessionId, setSessionId] = useState('');
  const [live, setLive] = useState<BoardSnapshot | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    retroHistory().then(
      (envelope) => {
        setRuns(envelope.data?.history ?? []);
        setSessionId(envelope.data?.session_id ?? '');
      },
      (e: Error) => setError(e.message),
    );
    loadBoards().then(
      (body) => setLive(body.boards.find((board) => board.kind === 'retro') ?? null),
      () => undefined,
    );
  }, []);

  async function start() {
    setBusy(true);
    setError('');
    try {
      const board = await startRetroBoard();
      window.location.hash = `#/humans/retro/board?id=${encodeURIComponent(board.board_id)}`;
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  if (error && !runs) return <NoticeBlock title="Could not load past retros" items={[error]} />;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Retro</h1>
          <p class="dash-sub">A live board your team fills in from their browsers, and every retro before it.</p>
        </div>
        <div class="dash-actions">
          {live ? (
            <a class="button primary" href={`#/humans/retro/board?id=${encodeURIComponent(live.board_id)}`}>
              Rejoin the live board
            </a>
          ) : (
            <button type="button" class="primary" disabled={busy} onClick={() => void start()}>
              {busy ? 'Opening…' : 'Start a retro'}
            </button>
          )}
        </div>
      </header>

      {error && <NoticeBlock title="Could not start the board" items={[error]} />}
      {!runs && <p>Loading…</p>}

      {runs && runs.length > 0 && (
        <div class="profile-list">
          {runs.map((run) => (
            <Card key={run.id} title={run.sprint_name || run.retro_date}>
              <StatGrid>
                <StatTile label="Date" value={run.retro_date} />
                <StatTile label="Cards" value={String(run.card_count ?? 0)} />
                <StatTile label="Actions" value={String(run.action_count ?? 0)} />
              </StatGrid>
              <ResultActions refer={{ kind: 'retro', session_id: sessionId, run_id: run.id }} mode="retro" />
            </Card>
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <Card title="No retros yet">
          <p>
            <Duck state="idle" size={28} /> Start a board and send the invite — everyone adds cards from their own
            browser, and yeaboi drafts the action items when you are done.
          </p>
        </Card>
      )}
    </div>
  );
}
