// The host controls a live board carries, whatever kind it is: the join code,
// the invite, the way into the board window, and the way out.
//
// Two rules this component exists to keep in one place:
//
// * the link's `notice` is rendered above everything else. It is non-empty only
//   for an expiry, and once a quick tunnel expires the invite already sent to
//   everyone is permanently dead — a sticky status line must not swallow that.
// * Copy invite hands over the participant link with the code in its fragment,
//   never the host link. The host link carries the admin secret, which would
//   make every reader a host.

import { NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { quip } from '../ambience';
import { type BoardSnapshot, boardInvite, closeBoard, loadBoard, openBoardWindow, retryLink } from '../boards';

/** How often a live board is re-read. The board itself pushes to the browsers
 *  that joined it; this is the host's own view catching up. */
const POLL_MS = 2000;

export function useBoard(boardId: string): [BoardSnapshot | null, string, () => void] {
  const [board, setBoard] = useState<BoardSnapshot | null>(null);
  const [error, setError] = useState('');

  function refresh() {
    if (!boardId) return;
    loadBoard(boardId).then(setBoard, (e: Error) => setError(e.message));
  }

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [boardId]);

  return [board, error, refresh];
}

export function BoardHost({ board, onClosed }: { board: BoardSnapshot; onClosed: (runId: number) => void }) {
  const [invite, setInvite] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (board.link.state !== 'ready') {
      setInvite('');
      return;
    }
    boardInvite(board.board_id).then((body) => setInvite(body.invite), () => undefined);
  }, [board.board_id, board.link.state]);

  async function copyInvite() {
    if (!invite) {
      // Never a half-invite: before the tunnel lands there is no address that
      // works for a reader, and a code alone sends the host into a chat window
      // with nothing to click.
      setMessage('The secure link is still starting — try again in a moment.');
      return;
    }
    await navigator.clipboard.writeText(invite);
    setMessage('Copied the invite to your clipboard.');
  }

  async function end() {
    setBusy(true);
    const result = await closeBoard(board.board_id);
    setBusy(false);
    // A finished poker table has a number to show for it; a retro's result is
    // the actions, which are drafted on the board rather than at the close.
    if (board.kind === 'poker') quip('poker_done');
    onClosed(result.run_id);
  }

  return (
    <div class="board-host">
      {board.link.notice && <NoticeBlock title="Secure link" items={[board.link.notice]} />}
      <dl class="share-facts">
        <dt>Join code</dt>
        <dd>{board.display_code}</dd>
        <dt>Participant link</dt>
        <dd>{board.share_url || board.link.status || 'starting…'}</dd>
      </dl>
      <div class="dash-actions">
        <button type="button" class="primary" onClick={() => void openBoardWindow(board.board_id)}>
          Open the board
        </button>
        <button type="button" onClick={() => void copyInvite()}>
          Copy invite
        </button>
        {board.link.failed && (
          <button
            type="button"
            disabled={board.link.starting}
            onClick={() => void retryLink(board.board_id).then(() => setMessage(''))}
          >
            Retry link
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => void end()}>
          {busy ? 'Closing…' : 'End session'}
        </button>
      </div>
      {message && <p class="dash-note">{message}</p>}
    </div>
  );
}

/** The `?id=` a board page was opened with. */
export function boardIdFromHash(): string {
  const query = window.location.hash.split('?')[1] ?? '';
  return new URLSearchParams(query).get('id') ?? '';
}
