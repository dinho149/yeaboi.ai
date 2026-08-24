// Ship — pick a story from the plan, name the repo, launch.
//
// The repo is resolved by the backend before anything starts: what a run writes
// to is the git toplevel, not the typed path, and that is also what the sandbox
// must have granted. A refusal here is a sentence, not a failed run.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import {
  type ShipSnapshot,
  type ShipStories,
  type ShipTarget,
  launchShip,
  loadShipRuns,
  loadStories,
  resolveRepo,
} from '../modes';

export function Ship() {
  const [plan, setPlan] = useState<ShipStories | null>(null);
  const [selected, setSelected] = useState(0);
  const [repo, setRepo] = useState('');
  const [check, setCheck] = useState('');
  const [target, setTarget] = useState<ShipTarget | null>(null);
  const [live, setLive] = useState<ShipSnapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    loadStories().then(
      (body) => {
        setPlan(body);
        setRepo(body.default_repo);
      },
      (e: Error) => setError(e.message),
    );
    loadShipRuns().then((body) => setLive(body.runs.filter((row) => !row.finished)), () => undefined);
  }, []);

  // Resolving costs a git call, so it follows the field rather than every
  // keystroke's render.
  useEffect(() => {
    if (!repo) return;
    let stale = false;
    const timer = window.setTimeout(() => {
      resolveRepo(repo).then(
        (found) => {
          if (!stale) setTarget(found);
        },
        () => undefined,
      );
    }, 400);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [repo]);

  if (error && !plan) return <NoticeBlock title="Could not open Ship" items={[error]} />;
  if (!plan) return <p>Loading…</p>;

  const story = plan.stories[selected];

  async function launch() {
    if (busy || !story) return;
    setBusy(true);
    setError('');
    try {
      const snapshot = await launchShip({
        story_id: story.id,
        story_title: story.title,
        repo,
        session_id: plan!.session_id,
        check_command: check,
      });
      window.location.hash = `#/humans/ship/run?key=${encodeURIComponent(snapshot.key)}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Ship</h1>
          <p class="dash-sub">A story from your plan, implemented behind your approval.</p>
        </div>
      </header>

      {error && <NoticeBlock title="Could not launch" items={[error]} />}
      {plan.problem && <NoticeBlock title="Saved plans" items={[plan.problem]} />}

      {live.length > 0 && (
        <Card title="Still running">
          <ul class="review-list">
            {live.map((row) => (
              <li key={row.key}>
                <a href={`#/humans/ship/run?key=${encodeURIComponent(row.key)}`}>
                  {row.story_title || row.story_id}
                </a>{' '}
                <span class="dash-note">{row.repo}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {plan.stories.length === 0 ? (
        <Card title="No stories yet">
          <p>
            <Duck state="idle" size={28} /> {plan.empty_message}
          </p>
        </Card>
      ) : (
        <>
          <Card title={plan.project_name || 'Latest plan'}>
            <div class="chip-row">
              {plan.stories.map((row, index) => (
                <label key={row.id} class="check-row">
                  <input
                    type="radio"
                    name="story"
                    checked={selected === index}
                    onChange={() => setSelected(index)}
                  />
                  <span>
                    <strong>{row.id}</strong> {row.title}
                    <span class="dash-note">
                      {row.points} pts · {row.criteria} acceptance criteria
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </Card>

          <Card title="Where">
            <div class="field-row">
              <label for="ship-repo">Repository</label>
              <input
                id="ship-repo"
                type="text"
                value={repo}
                onInput={(e) => setRepo((e.target as HTMLInputElement).value)}
              />
            </div>
            <div class="field-row">
              <label for="ship-check">Check command</label>
              <input
                id="ship-check"
                type="text"
                value={check}
                placeholder="make test (optional)"
                onInput={(e) => setCheck((e.target as HTMLInputElement).value)}
              />
            </div>
            {target && (
              <StatGrid>
                <StatTile label="Resolves to" value={target.repo || '—'} />
                <StatTile label="Granted" value={target.allowed ? 'yes' : 'no'} />
              </StatGrid>
            )}
            {target?.problem && <NoticeBlock title="That repository is not ready" items={[target.problem]} />}
            {target?.consent_hint && <NoticeBlock title="Not granted yet" items={[target.consent_hint]} />}
          </Card>

          <div class="dash-actions">
            <button
              type="button"
              class="primary"
              disabled={busy || !story || !target?.allowed || Boolean(target?.problem)}
              onClick={() => void launch()}
            >
              {busy ? 'Launching…' : 'Launch'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
