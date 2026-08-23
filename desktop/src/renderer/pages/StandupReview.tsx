// Transcript review — check a standup report against the meeting that
// discussed it, and see which diagnosed gaps have been filed.
//
// Filing writes public GitHub issues, so it is a separate, explicit act with
// its own button; the review itself never publishes.

import { Card, Lozenge, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { callTool } from '../api';
import { loadStandup } from '../dashboards';

interface Gap {
  fingerprint: string;
  category: string;
  scope: string;
  title: string;
  detail: string;
  root_cause: string;
  priority: string;
}

interface Review {
  review_id: number;
  standup_date: string;
  reviewed_at: string;
  gaps: Gap[];
  config_suggestions: Gap[];
  accuracy_note: string;
  claims_matched: number;
  claims_missing: number;
  claims_contradicted: number;
}

interface GapsView {
  session_id: string;
  latest_review: Review | null;
  gap_issues: { fingerprint?: string; issue_number?: number; title?: string }[];
  nudge: { missed_dates: string[]; level?: string } | null;
}

const PRIORITY_TONE: Record<string, 'blocked' | 'inprogress' | 'todo'> = {
  critical: 'blocked',
  high: 'blocked',
  medium: 'inprogress',
  low: 'todo',
};

export function StandupReview() {
  const [sessionId, setSessionId] = useState('');
  const [view, setView] = useState<GapsView | null>(null);
  const [paste, setPaste] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');

  useEffect(() => {
    loadStandup().then(
      (dash) => {
        setSessionId(dash.session_id);
        void refresh(dash.session_id);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  async function refresh(id: string) {
    const envelope = await callTool<GapsView>('standup_gaps', { session_id: id });
    if (envelope.ok) setView(envelope.data);
    else setError(envelope.error?.message ?? 'standup_gaps failed');
  }

  async function review(args: object, label: string) {
    if (busy) return;
    setBusy(label);
    setError('');
    setNote('');
    const envelope = await callTool('standup_review', { session_id: sessionId, ...args });
    setBusy('');
    if (!envelope.ok) {
      setError(envelope.error?.message ?? 'standup_review failed');
      return;
    }
    setPaste('');
    setNote(label === 'file' ? 'Filed. Issue numbers appear in the ledger below.' : 'Reviewed.');
    await refresh(sessionId);
  }

  if (error && !view) return <NoticeBlock title="Could not load the review" items={[error]} />;
  if (!view) return <p>Loading…</p>;

  const latest = view.latest_review;
  const filed = new Set(view.gap_issues.filter((e) => e.issue_number).map((e) => e.fingerprint));

  return (
    <div class="dash">
      <h1 class="page-title">Transcript review</h1>
      <p class="dash-sub">
        What the standup missed, and why — a missing integration, an unconfigured source, or a summary that dropped
        what it collected.
      </p>

      {view.nudge?.missed_dates.length ? (
        <NoticeBlock
          title="Unchecked standups"
          items={[
            `${view.nudge.missed_dates.length} standup${
              view.nudge.missed_dates.length === 1 ? '' : 's'
            } ran without being checked against their meeting — oldest ${
              view.nudge.missed_dates[view.nudge.missed_dates.length - 1]
            }.`,
          ]}
        />
      ) : null}

      <Card title="Review a meeting">
        <textarea
          rows={6}
          placeholder="Paste the transcript here, or use Sweep to read ~/.yeaboi/transcripts…"
          value={paste}
          onInput={(e) => setPaste((e.target as HTMLTextAreaElement).value)}
        />
        <div class="dash-actions">
          <button
            type="button"
            class="primary"
            disabled={!!busy || !paste.trim()}
            onClick={() => void review({ transcript_text: paste }, 'paste')}
          >
            {busy === 'paste' ? 'Reviewing…' : 'Review this transcript'}
          </button>
          <button type="button" disabled={!!busy} onClick={() => void review({}, 'sweep')}>
            {busy === 'sweep' ? 'Sweeping…' : 'Sweep the transcript folder'}
          </button>
        </div>
      </Card>

      {error && <NoticeBlock title="That review did not finish" items={[error]} />}
      {note && <p class="dash-note">{note}</p>}

      {latest ? (
        <>
          <Card title={`Latest review · ${latest.standup_date}`}>
            <p>
              {latest.claims_matched} matched · {latest.claims_missing} missing · {latest.claims_contradicted}{' '}
              contradicted
            </p>
            {latest.accuracy_note && <p class="dash-note">{latest.accuracy_note}</p>}
          </Card>

          <Card
            title="Gaps in yeaboi"
            actions={
              latest.gaps.length ? (
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => void review({ file_issues: true, include_reviewed: true }, 'file')}
                >
                  {busy === 'file' ? 'Filing…' : 'File as GitHub issues'}
                </button>
              ) : undefined
            }
          >
            {latest.gaps.length ? (
              <ul class="gap-list">
                {latest.gaps.map((gap) => (
                  <li key={gap.fingerprint}>
                    <Lozenge category={PRIORITY_TONE[gap.priority] ?? 'todo'} small>
                      {gap.priority}
                    </Lozenge>
                    <strong>{gap.title}</strong>
                    {filed.has(gap.fingerprint) && <span class="dash-note"> · filed</span>}
                    <p>{gap.detail}</p>
                    {gap.root_cause && <p class="dash-note">{gap.root_cause}</p>}
                  </li>
                ))}
              </ul>
            ) : (
              <p>Nothing to file — the report covered what the meeting discussed.</p>
            )}
            <p class="dash-note">Filing writes public issues on the yeaboi repo. Nothing is sent until you press it.</p>
          </Card>

          <Card title="Fix in your config">
            {latest.config_suggestions.length ? (
              <ul class="gap-list">
                {latest.config_suggestions.map((gap) => (
                  <li key={gap.fingerprint}>
                    <strong>{gap.title}</strong>
                    <p>{gap.detail}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p>No configuration changes suggested.</p>
            )}
          </Card>
        </>
      ) : (
        <Card title="No review yet">
          <p>Paste a transcript above, or drop one into ~/.yeaboi/transcripts and sweep.</p>
        </Card>
      )}

      <p>
        <a href="#/humans/standup">Back to the standup</a>
      </p>
    </div>
  );
}
