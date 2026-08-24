// The finished plan — read it, save it, publish it, push it to a board.
//
// Until this page existed the desktop could build a plan and never get it out
// again: four of planning's tools (plan_get, plan_export, plan_publish,
// plan_sync) had no window to be called from.
//
// Publish and sync both write somewhere real, so neither runs on a click
// alone — each asks once, in the words of what it is about to do.

import { Card, Lozenge, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import type { Envelope } from '../api';
import {
  PLAN_DESTINATIONS,
  PLAN_FORMATS,
  PLAN_TRACKERS,
  type Plan,
  exportPlan,
  isEmptyPlan,
  loadPlan,
  outcomeMessage,
  planCounts,
  publishPlan,
  storiesOf,
  syncPlan,
} from '../plan';

/** The plan's session id rides the hash query, like the chat's. */
export function sessionIdFromHash(hash: string): string {
  const query = hash.indexOf('?');
  if (query < 0) return '';
  return new URLSearchParams(hash.slice(query + 1)).get('id') ?? '';
}

type Pending = { verb: string; run: () => Promise<void>; warning: string } | null;

export function PlanView() {
  const [sessionId] = useState(() => sessionIdFromHash(window.location.hash));
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<Pending>(null);

  useEffect(() => {
    loadPlan(sessionId).then(
      (envelope) => {
        if (!envelope.ok) {
          setError(envelope.error?.message ?? 'plan_get failed');
          return;
        }
        setPlan(envelope.data);
      },
      (e: Error) => setError(e.message),
    );
  }, [sessionId]);

  async function run(label: string, call: () => Promise<Envelope<Record<string, unknown>>>) {
    setBusy(label);
    setMessage('');
    setWarnings([]);
    try {
      const envelope = await call();
      setMessage(outcomeMessage(envelope));
      setWarnings(envelope.warnings ?? []);
    } catch (e) {
      setMessage((e as Error).message);
    }
    setBusy('');
  }

  if (error) return <p class="chat-error">Could not open the plan: {error}</p>;
  if (!plan) return <p>Loading…</p>;

  const id = plan.session_id ?? sessionId;
  const counts = planCounts(plan);
  const project = plan.project ?? {};

  return (
    <div>
      <h1 class="page-title">{project.name || 'The plan'}</h1>
      {project.description && <p class="dash-sub">{project.description}</p>}

      {isEmptyPlan(plan) ? (
        <NoticeBlock
          title="Nothing to show yet"
          items={[
            'This conversation has not produced a plan yet — finish the intake and the epics, stories, tasks and sprints appear here.',
          ]}
        />
      ) : (
        <>
          <Card title="What is in it">
            <div class="plan-counts">
              <span>{counts.epics} epics</span>
              <span>{counts.stories} stories</span>
              <span>{counts.tasks} tasks</span>
              <span>{counts.sprints} sprints</span>
              {project.tech_stack?.length ? <span>{project.tech_stack.join(' · ')}</span> : null}
            </div>
          </Card>

          {(plan.sprints ?? []).map((sprint, index) => (
            <Card key={`${sprint.name ?? 'sprint'}-${index}`} title={sprint.name || `Sprint ${index + 1}`}>
              {sprint.goal && <p>{sprint.goal}</p>}
              <ul class="plan-stories">
                {storiesOf(plan, sprint).map((story) => (
                  <li key={story.id}>
                    <Lozenge category="todo">{story.story_points ?? 0}</Lozenge> {story.title || story.id}
                  </li>
                ))}
              </ul>
            </Card>
          ))}
        </>
      )}

      <Card title="Save it">
        <div class="dash-actions">
          {PLAN_FORMATS.map((format) => (
            <button
              key={format.key}
              type="button"
              disabled={Boolean(busy)}
              title={format.note}
              onClick={() => void run(format.key, () => exportPlan(id, format.key))}
            >
              {busy === format.key ? 'Saving…' : format.label}
            </button>
          ))}
        </div>
      </Card>

      <Card title="Publish it as a page">
        <div class="dash-actions">
          {PLAN_DESTINATIONS.map((destination) => (
            <button
              key={destination.key}
              type="button"
              disabled={Boolean(busy)}
              onClick={() =>
                setConfirm({
                  verb: `Publish to ${destination.label}`,
                  warning: `This creates a page in your ${destination.label} workspace.`,
                  run: () => run(destination.key, () => publishPlan(id, destination.key, 'plan')),
                })
              }
            >
              {busy === destination.key ? 'Publishing…' : destination.label}
            </button>
          ))}
        </div>
      </Card>

      <Card title="Push it to your board">
        <div class="dash-actions">
          {PLAN_TRACKERS.map((tracker) => (
            <button
              key={tracker.key}
              type="button"
              disabled={Boolean(busy)}
              onClick={() =>
                setConfirm({
                  verb: `Push to ${tracker.label}`,
                  warning: `This creates ${counts.stories} stories and ${counts.tasks} tasks as real tickets in ${tracker.label}. Re-running skips anything it already made.`,
                  run: () => run(tracker.key, () => syncPlan(id, tracker.key, '')),
                })
              }
            >
              {busy === tracker.key ? 'Pushing…' : tracker.label}
            </button>
          ))}
        </div>
      </Card>

      {message && <p class="plan-outcome">{message}</p>}
      {warnings.length > 0 && <NoticeBlock title="Notices" items={warnings} />}

      {confirm && (
        <div class="scrim">
          <div class="modal" role="dialog" aria-modal="true" aria-label={confirm.verb}>
            <header class="modal-head">
              <h2>{confirm.verb}?</h2>
            </header>
            <p>{confirm.warning}</p>
            <div class="modal-actions">
              <button
                type="button"
                class="primary"
                onClick={() => {
                  const pending = confirm;
                  setConfirm(null);
                  void pending.run();
                }}
              >
                {confirm.verb}
              </button>
              <button type="button" onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
