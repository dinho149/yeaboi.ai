// The live retro board's host view — the cards as they arrive, the invite, and
// the two things only the host does: draft the action items, and end it.
//
// The board itself is a separate top-level window (a board page refuses to be
// framed). This page is the ceremony's control strip.

import { Card, NoticeBlock } from '@design/primitives';
import { useState } from 'react';
import { quip } from '../ambience';
import { type RetroBoardState, type RetroCard, generateActionItems, maskText } from '../boards';
import { BoardHost, boardIdFromHash, useBoard } from '../components/BoardHost';
import { ResultActions } from '../components/ResultActions';

const GRID_TITLES: Record<string, string> = {
  went_well: 'Went well',
  didnt_go_well: "Didn't go well",
  action_items: 'Action items',
  demos: 'Demos',
};

export function RetroBoard() {
  const boardId = boardIdFromHash();
  const [board, error, refresh] = useBoard(boardId);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [closed, setClosed] = useState(0);
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');

  async function draft() {
    if (!board) return;
    setBusy(true);
    try {
      const result = await generateActionItems(board.board_id);
      setMessage(result.message);
      quip('actions_done');
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    }
    setBusy(false);
  }

  if (closed) {
    return (
      <Card title="Retro recorded">
        <p>The board is closed and the retro is saved.</p>
        <a class="button primary" href="#/humans/retro">
          Back to the retros
        </a>
      </Card>
    );
  }
  if (error) return <NoticeBlock title="This board is not live" items={[error]} />;
  if (!boardId) return <NoticeBlock title="No board" items={['Start one from the Retro page.']} />;
  if (!board) return <p>Loading…</p>;

  const state = board.state as unknown as RetroBoardState;
  const grids = state.grids ?? {};

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Retro — {board.title}</h1>
          <p class="dash-sub">Everyone adds cards from their own browser; the board updates as they land.</p>
        </div>
      </header>

      <BoardHost board={board} onClosed={(runId) => setClosed(runId || -1)} />

      <div class="dash-actions">
        <button type="button" class="primary" disabled={busy} onClick={() => void draft()}>
          {busy ? 'Drafting…' : 'Generate action items'}
        </button>
      </div>
      {message && <p class="dash-note">{message}</p>}

      <ResultActions
        refer={{ kind: 'retro', session_id: board.session_id }}
        mode="retro"
        anonNote={anonNote}
        onAnonymize={(replacements, note) => {
          setMask(replacements);
          setAnonNote(note);
        }}
      />

      <div class="retro-grids">
        {Object.entries(grids).map(([key, cards]) => (
          <Card key={key} title={`${GRID_TITLES[key] ?? key} (${cards.length})`}>
            {cards.length === 0 ? (
              <p class="dash-note">Nothing yet.</p>
            ) : (
              <ul class="retro-cards">
                {cards.map((card: RetroCard) => (
                  <li key={card.id}>
                    <span>{maskText(card.text, mask)}</span>
                    <span class="dash-note"> — {maskText(card.author, mask)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        ))}
      </div>

      {state.carried?.length > 0 && (
        <Card title="Last sprint's actions">
          <ul class="retro-cards">
            {state.carried.map((item, index) => (
              <li key={index}>{maskText(item.text, mask)}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
