// The live poker table's host view — where the room is up to, the invite, and
// the way out. The voting itself happens in the board window (and in every
// participant's browser); this page is the host's strip beside it.
//
// Vote secrecy is the board's own rule, not this page's: while the phase is
// `voting` the snapshot carries who has voted and nothing about what they
// voted, so there is nothing here to leak.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { useState } from 'react';
import { BoardHost, boardIdFromHash, useBoard } from '../components/BoardHost';

interface PokerState {
  phase: string;
  ticket_index: number;
  ticket_count: number;
  ticket: { key?: string; summary?: string } | null;
  progress: { estimated: number; total: number };
  presence: { name: string; avatar: string }[];
  votes: { name: string; voted?: boolean; value?: string }[];
  median: number | null;
  suggestion: string | null;
  notice?: string;
}

export function PokerBoard() {
  const boardId = boardIdFromHash();
  const [board, error] = useBoard(boardId);
  const [closed, setClosed] = useState(0);

  if (closed) {
    return (
      <Card title="Session recorded">
        <p>The table is closed and the estimates are saved.</p>
        <a class="button primary" href="#/humans/poker">
          Back to the sessions
        </a>
      </Card>
    );
  }
  if (error) return <NoticeBlock title="This table is not live" items={[error]} />;
  if (!boardId) return <NoticeBlock title="No table" items={['Start one from the Poker page.']} />;
  if (!board) return <p>Loading…</p>;

  const state = board.state as unknown as PokerState;
  const voting = state.phase === 'voting';

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Poker — {board.title}</h1>
          <p class="dash-sub">Everyone votes at once, so nobody anchors on whoever spoke first.</p>
        </div>
      </header>

      <BoardHost board={board} onClosed={(runId) => setClosed(runId || -1)} />

      {state.notice && <NoticeBlock title="Table" items={[state.notice]} />}

      <Card title={state.ticket ? `${state.ticket.key ?? ''} ${state.ticket.summary ?? ''}` : 'No ticket'}>
        <StatGrid>
          <StatTile label="Ticket" value={`${state.ticket_index + 1} / ${state.ticket_count}`} />
          <StatTile label="Estimated" value={`${state.progress.estimated} / ${state.progress.total}`} />
          <StatTile label="At the table" value={String(state.presence.length)} />
          <StatTile label="Phase" value={voting ? 'voting' : 'revealed'} />
        </StatGrid>
        {!voting && state.suggestion && (
          <p class="dash-note">
            Median {state.median ?? '—'} · suggestion <strong>{state.suggestion}</strong>
          </p>
        )}
        <ul class="retro-cards">
          {state.votes.map((vote) => (
            <li key={vote.name}>
              {vote.name}
              {/* While voting, the board sends who has voted and nothing more. */}
              <span class="dash-note"> — {voting ? (vote.voted ? 'voted' : 'thinking…') : (vote.value ?? '—')}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
