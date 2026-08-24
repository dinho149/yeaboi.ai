// One ship run: the phase checklist, the approval gate, the result.
//
// The run lives in the backend, so this page polls rather than streams — a
// reload walks back into the run it left instead of abandoning a coding agent
// mid-diff. The gate is answered through the store, which arbitrates whoever
// answers first: `taken: false` means somebody else got there.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { useEffect, useState } from 'react';
import { type ShipSnapshot, answerGate, cancelShip, loadShipRun, shipKeyFromHash } from '../modes';

const POLL_MS = 1500;

export function ShipRun() {
  const key = shipKeyFromHash(window.location.hash);
  const [run, setRun] = useState<ShipSnapshot | null>(null);
  const [comment, setComment] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!key) return;
    let stop = false;
    const tick = () => {
      loadShipRun(key).then(
        (snapshot) => {
          if (stop) return;
          setRun(snapshot);
          // Polling stops when the run does — nothing about a finished run
          // changes, and a timer that never clears is a leak per visit.
          if (!snapshot.finished) window.setTimeout(tick, POLL_MS);
        },
        (e: Error) => {
          if (!stop) setError(e.message);
        },
      );
    };
    tick();
    return () => {
      stop = true;
    };
  }, [key]);

  if (!key) return <NoticeBlock title="No run" items={['Launch one from Ship first.']} />;
  if (error && !run) return <NoticeBlock title="Could not read the run" items={[error]} />;
  if (!run) return <p>Loading…</p>;

  async function resolve(resolution: 'approved' | 'rejected') {
    try {
      const answer = await answerGate(key, resolution, resolution === 'rejected' ? comment : '');
      setMessage(answer.taken ? `Gate ${resolution}.` : 'The gate was already answered.');
      setRejecting(false);
      setComment('');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const result = run.result as Record<string, string> | null;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">{run.story_title || run.story_id}</h1>
          <p class="dash-sub">{run.repo}</p>
        </div>
        <div class="dash-actions">
          {!run.finished && (
            <button type="button" disabled={run.cancelling} onClick={() => void cancelShip(key)}>
              {run.cancelling ? 'Stopping…' : 'Stop'}
            </button>
          )}
          <a class="button" href="#/humans/ship">
            Back
          </a>
        </div>
      </header>

      {message && <p class="dash-note">{message}</p>}
      {error && <NoticeBlock title="That did not work" items={[error]} />}
      {run.failure && <NoticeBlock title="The run stopped" items={[run.failure]} />}

      {run.board.url && (
        <Card title="Watching along">
          <p>
            <a href={run.board.url}>{run.board.url}</a> · code <strong>{run.board.code}</strong>
          </p>
        </Card>
      )}

      <Card title={run.finished ? 'Phases' : 'Working…'}>
        <ul class="review-list">
          {run.phases.map((phase) => (
            <li key={phase.component_id}>
              <strong>{phase.label}</strong> <span class="dash-note">{phase.status}</span>
            </li>
          ))}
          {run.phases.length === 0 && <li class="dash-note">Setting up…</li>}
        </ul>
      </Card>

      {run.gate && (
        <Card title="Your approval">
          <StatGrid>
            <StatTile label="Branch" value={run.gate.branch || '—'} />
            <StatTile label="Diff" value={run.gate.diff_stat || '—'} />
            <StatTile label="Cost" value={`$${(run.gate.cost_usd ?? 0).toFixed(2)}`} />
          </StatGrid>
          <pre class="diff-pane">{run.gate.diff_text}</pre>
          {rejecting ? (
            <div class="field-row">
              <input
                type="text"
                value={comment}
                placeholder="What should change?"
                onInput={(e) => setComment((e.target as HTMLInputElement).value)}
              />
              <button type="button" onClick={() => void resolve('rejected')}>
                Send rejection
              </button>
            </div>
          ) : (
            <div class="dash-actions">
              <button type="button" class="primary" onClick={() => void resolve('approved')}>
                Approve
              </button>
              <button type="button" onClick={() => setRejecting(true)}>
                Reject
              </button>
            </div>
          )}
        </Card>
      )}

      {result && (
        <Card title="Result">
          <StatGrid>
            <StatTile label="Status" value={String(result.status ?? '')} />
            <StatTile label="Branch" value={String(result.branch ?? '—')} />
            <StatTile label="Cost" value={`$${Number(result.cost_usd ?? 0).toFixed(2)}`} />
          </StatGrid>
          {result.pr_url && (
            <p>
              <a href={String(result.pr_url)}>{String(result.pr_url)}</a>
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
