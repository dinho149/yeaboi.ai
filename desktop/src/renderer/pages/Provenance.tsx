// Provenance — has anyone edited the record, what was decided, and why.
//
// The chain records itself while standups and performance workflows run; until
// now the only way to read it back was the CLI or an MCP client. The order here
// is the audit's own: verification first, because a window of decisions from a
// chain that cannot be trusted is worse than no window at all.

import { Card, DataTable, Lozenge, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type ProvenanceAudit, type ProvenanceTrace, provenanceAudit, provenanceTrace } from '../ops';

const WINDOWS = [7, 30, 90];

export function Provenance() {
  const [windowDays, setWindowDays] = useState(30);
  const [audit, setAudit] = useState<ProvenanceAudit | null>(null);
  const [trace, setTrace] = useState<ProvenanceTrace | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setAudit(null);
    provenanceAudit(windowDays).then(
      (envelope) => setAudit(envelope.data),
      (e: Error) => setError(e.message),
    );
  }, [windowDays]);

  function open(entityId: string) {
    setTrace(null);
    provenanceTrace(entityId).then(
      (envelope) => setTrace(envelope.data),
      (e: Error) => setError(e.message),
    );
  }

  if (error) return <NoticeBlock title="Could not read the decision chain" items={[error]} />;
  if (!audit) return <p>Loading…</p>;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Provenance</h1>
          <p class="dash-sub">The tamper-evident record of what was decided, by whom, and on what.</p>
        </div>
        <div class="chip-row">
          {WINDOWS.map((days) => (
            <label key={days} class="check-row">
              <input
                type="radio"
                name="window"
                checked={windowDays === days}
                onChange={() => setWindowDays(days)}
              />
              <span>{days} days</span>
            </label>
          ))}
        </div>
      </header>

      {audit.warnings.length > 0 && <NoticeBlock title="Read this first" items={audit.warnings} />}

      <Card
        title={audit.chain_valid ? 'The record is intact' : 'The record has been changed'}
        actions={
          <Lozenge category={audit.chain_valid ? 'done' : 'blocked'}>
            {audit.chain_valid ? 'verified' : 'broken'}
          </Lozenge>
        }
      >
        <StatGrid>
          <StatTile label="Decisions recorded" value={String(audit.total_records)} />
          <StatTile label="In this window" value={String(audit.window_records)} />
          <StatTile label="Breaks" value={String(audit.breaks.length)} />
        </StatGrid>
        {audit.breaks.length > 0 && (
          <NoticeBlock
            title="Where the chain breaks"
            items={audit.breaks.map(([sequence, entity, reason]) => `#${sequence} ${entity}: ${reason}`)}
          />
        )}
      </Card>

      {audit.records_by_type.length > 0 && (
        <Card title="What kinds of decisions exist">
          <DataTable
            rows={audit.records_by_type}
            rowKey={(row) => row[0]}
            columns={[
              { key: 'kind', header: 'Kind', cell: (row) => row[0] },
              { key: 'count', header: 'Records', numeric: true, cell: (row) => row[1] },
            ]}
          />
        </Card>
      )}

      <Card title="Lately">
        {audit.recent.length === 0 ? (
          <p>
            <Duck state="idle" size={28} /> Nothing in this window. Run a standup or a performance workflow and
            the trail starts itself.
          </p>
        ) : (
          <DataTable
            rows={audit.recent}
            rowKey={(row) => String(row.sequence_id)}
            caption="Newest first. Pick a row to see what it was decided on."
            columns={[
              { key: 'when', header: 'When', cell: (row) => row.timestamp },
              {
                key: 'entity',
                header: 'Entity',
                cell: (row) => (
                  <button type="button" class="link-button" onClick={() => open(row.entity_id)}>
                    {row.entity_id}
                  </button>
                ),
              },
              { key: 'kind', header: 'Decision', cell: (row) => row.record_kind },
              { key: 'agent', header: 'By', cell: (row) => `${row.agent_id} (${row.role})` },
              { key: 'detail', header: 'Detail', cell: (row) => row.detail },
            ]}
          />
        )}
      </Card>

      {trace && (
        <Card title={`Why: ${trace.entity_id}`}>
          {!trace.found ? (
            <NoticeBlock title="Nothing recorded" items={trace.warnings} />
          ) : (
            <ul class="review-list">
              {trace.records.map((row) => (
                <li key={row.sequence_id}>
                  <strong>{row.record_kind}</strong> · {row.timestamp} · {row.agent_id}
                  <p class="dash-note">{row.detail}</p>
                  {row.inputs.length > 0 && <p class="dash-note">on: {row.inputs.join(', ')}</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
