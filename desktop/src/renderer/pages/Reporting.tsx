// Reporting — the saved-reports hub, and the way into a new one.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { maskText } from '../boards';
import { type ReportRun, reportingHistory } from '../modes';
import { ResultActions } from '../components/ResultActions';

export function Reporting() {
  const [runs, setRuns] = useState<ReportRun[] | null>(null);
  const [error, setError] = useState('');
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');

  useEffect(() => {
    reportingHistory().then(
      (envelope) => setRuns(envelope.data?.history ?? []),
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error && !runs) return <NoticeBlock title="Could not load past reports" items={[error]} />;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Reporting</h1>
          <p class="dash-sub">What the team delivered, written for the business.</p>
        </div>
        <div class="dash-actions">
          <a class="button primary" href="#/humans/reporting/new">
            New report
          </a>
          <a class="button" href="#/humans/reporting/style">
            Deck style
          </a>
        </div>
      </header>

      {anonNote && <NoticeBlock title={anonNote} items={['Review before sharing.']} />}
      {!runs && <p>Loading…</p>}

      {runs && runs.length > 0 && (
        <div class="profile-list">
          {runs.map((run) => (
            <Card key={run.id} title={maskText(run.period || run.period_end, mask)}>
              <StatGrid>
                <StatTile label="Period end" value={run.period_end} />
                <StatTile label="Items" value={String(run.item_count ?? 0)} />
                <StatTile label="Project" value={maskText(run.project_name || '—', mask)} />
              </StatGrid>
              <ResultActions
                refer={{ kind: 'reporting', session_id: '', run_id: run.id }}
                mode="reporting"
                anonNote={anonNote}
                onAnonymize={(replacements, note) => {
                  setMask(replacements);
                  setAnonNote(note);
                }}
              />
            </Card>
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <Card title="No reports yet">
          <p>
            <Duck state="idle" size={28} /> Pick a period and yeaboi gathers what actually shipped, writes the
            narrative, and lays it out as a deck you can present.
          </p>
        </Card>
      )}
    </div>
  );
}
