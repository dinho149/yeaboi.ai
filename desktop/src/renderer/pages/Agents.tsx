// The Agents family — one page over four modes, addressed by the hash.
//
// The rule the terminal established and this keeps: the page opens on the last
// saved report, stamped with its age, while a fresh pass runs behind it. A scan
// reads every session log on the machine, so a loading screen would be the
// normal experience rather than the first-run one.

import { Card, DataTable, Lozenge, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import {
  type AgentModes,
  type AgentRunState,
  agentKindFromHash,
  emptyAgentRun,
  exportAgentReport,
  loadAgentLatest,
  loadAgentModes,
  reduceAgentRun,
  runAgentMode,
} from '../ops';

type Report = Record<string, unknown>;

const num = (report: Report, field: string): number => Number(report[field] ?? 0);
const text = (report: Report, field: string): string => String(report[field] ?? '');
const rows = (report: Report, field: string): Report[] => (report[field] as Report[]) ?? [];
const lines = (report: Report, field: string): string[] => (report[field] as string[]) ?? [];
const money = (value: number): string => `$${value.toFixed(2)}`;

function severityCategory(severity: string): 'todo' | 'inprogress' | 'done' | 'blocked' {
  if (severity === 'critical' || severity === 'high') return 'blocked';
  if (severity === 'medium') return 'inprogress';
  return 'todo';
}

export function Agents() {
  const kind = agentKindFromHash(window.location.hash);
  const [modes, setModes] = useState<AgentModes | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [asOf, setAsOf] = useState('');
  const [run, setRun] = useState<AgentRunState>(emptyAgentRun);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    loadAgentModes().then(setModes, (e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    setReport(null);
    setAsOf('');
    setNotice('');
    loadAgentLatest(kind).then(
      (latest) => {
        setReport(latest.report);
        setAsOf(latest.as_of);
        void refresh();
      },
      (e: Error) => setError(e.message),
    );
    // The hash is the only input; a mode change re-runs the whole open.
  }, [kind]);

  async function refresh() {
    setRefreshing(true);
    setRun(emptyAgentRun());
    let state = emptyAgentRun();
    try {
      await runAgentMode(kind, (line) => {
        state = reduceAgentRun(state, line);
        setRun(state);
      });
      if (state.report) {
        setReport(state.report);
        setAsOf('');
      } else if (state.error) {
        // A failed background refresh keeps the stale report on screen: losing a
        // good dashboard over it is the worse outcome.
        setNotice(state.error);
      }
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }

  const mode = modes?.modes.find((option) => option.kind === kind || option.key === kind);
  if (error) return <NoticeBlock title="Could not open this mode" items={[error]} />;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">{mode?.label ?? 'Agents'}</h1>
          <p class="dash-sub">{mode?.blurb ?? ''}</p>
        </div>
        <div class="dash-actions">
          <button
            type="button"
            disabled={!report}
            onClick={() =>
              void exportAgentReport(kind, 'copy').then(
                (result) => void navigator.clipboard.writeText(result.markdown ?? '').then(
                  () => setNotice('Copied the report to the clipboard.'),
                  () => setNotice('Could not reach the clipboard.'),
                ),
                (e: Error) => setNotice(e.message),
              )
            }
          >
            Copy
          </button>
          <button
            type="button"
            disabled={!report}
            onClick={() =>
              void exportAgentReport(kind, 'files').then(
                (result) => setNotice(result.message ?? 'Exported.'),
                (e: Error) => setNotice(e.message),
              )
            }
          >
            Export
          </button>
          <button type="button" class="primary" disabled={refreshing} onClick={() => void refresh()}>
            {refreshing ? 'Refreshing…' : 'Re-run'}
          </button>
        </div>
      </header>

      {modes && <p class="dash-note">{modes.beta_notice}</p>}
      {notice && <NoticeBlock title="Note" items={[notice]} />}
      {asOf && refreshing && <p class="dash-note">Showing the report saved at {asOf} while a fresh pass runs.</p>}

      {(refreshing || !report) && (run.components.length > 0 || run.phases.length > 0) && (
        <Card title="Scanning">
          <ul class="phase-list">
            {run.components.map((component) => (
              <li key={component.component_id}>
                {component.label} — {component.status}
                {component.total ? ` (${component.current ?? 0}/${component.total})` : ''}
              </li>
            ))}
            {run.phases.map((phase, index) => (
              <li key={`${phase}-${index}`}>{phase}</li>
            ))}
          </ul>
        </Card>
      )}

      {!report ? (
        <Card title="Nothing yet">
          <p>
            <Duck state="idle" size={28} /> The first pass reads every session log on this machine — it takes a
            moment.
          </p>
        </Card>
      ) : (
        <ReportView kind={kind} report={report} />
      )}
    </div>
  );
}

function ReportView({ kind, report }: { kind: string; report: Report }) {
  const warnings = lines(report, 'warnings');
  return (
    <>
      {warnings.length > 0 && <NoticeBlock title="Read this first" items={warnings} />}
      {kind === 'usage' && <UsageView report={report} />}
      {kind === 'advisor' && <AdvisorView report={report} />}
      {kind === 'standup' && <StandupView report={report} />}
      {kind === 'security' && <SecurityView report={report} />}
      <Advice report={report} />
    </>
  );
}

function Advice({ report }: { report: Report }) {
  const insights = lines(report, 'insights');
  const recommendations = lines(report, 'recommendations');
  if (insights.length === 0 && recommendations.length === 0) return null;
  return (
    <Card title="What to do about it">
      {insights.length > 0 && (
        <ul class="review-list">
          {insights.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {recommendations.length > 0 && (
        <ul class="review-list">
          {recommendations.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function UsageView({ report }: { report: Report }) {
  return (
    <>
      <Card title={`${text(report, 'period_start')} → ${text(report, 'period_end')}`}>
        <StatGrid>
          <StatTile label="Estimated spend" value={money(num(report, 'total_cost_usd'))} />
          <StatTile label="Sessions" value={String(num(report, 'session_count'))} />
          <StatTile label="Input tokens" value={num(report, 'total_input_tokens').toLocaleString()} />
          <StatTile label="Output tokens" value={num(report, 'total_output_tokens').toLocaleString()} />
        </StatGrid>
        <p class="dash-note">Rates as of {text(report, 'pricing_as_of') || 'unknown'}.</p>
      </Card>
      <Card title="By model">
        <DataTable
          rows={rows(report, 'by_model')}
          rowKey={(row) => String(row['model'])}
          empty="No model activity in the window."
          columns={[
            { key: 'model', header: 'Model', cell: (row) => String(row['model']) },
            { key: 'calls', header: 'Calls', numeric: true, cell: (row) => Number(row['calls']) },
            { key: 'cost', header: 'Cost', numeric: true, cell: (row) => money(Number(row['cost_usd'])) },
          ]}
        />
      </Card>
      <Card title="By project">
        <DataTable
          rows={rows(report, 'by_project')}
          rowKey={(row) => String(row['key'])}
          empty="No project activity in the window."
          columns={[
            { key: 'key', header: 'Project', cell: (row) => String(row['key']) },
            { key: 'sessions', header: 'Sessions', numeric: true, cell: (row) => Number(row['sessions']) },
            { key: 'cost', header: 'Cost', numeric: true, cell: (row) => money(Number(row['cost_usd'])) },
          ]}
        />
      </Card>
    </>
  );
}

function AdvisorView({ report }: { report: Report }) {
  return (
    <>
      <Card title="Recoverable spend">
        <StatGrid>
          <StatTile label="Recoverable" value={money(num(report, 'recoverable_usd'))} />
          <StatTile label="Of window spend" value={`${Math.round(num(report, 'recoverable_share') * 100)}%`} />
          <StatTile label="Window spend" value={money(num(report, 'total_cost_usd'))} />
          <StatTile label="Alignment" value={`${num(report, 'alignment_score')}/100`} />
        </StatGrid>
      </Card>
      <Card title="Where it went">
        <DataTable
          rows={rows(report, 'line_items')}
          rowKey={(row) => String(row['mechanism'])}
          empty="Nothing recoverable found."
          columns={[
            { key: 'label', header: 'Mechanism', cell: (row) => String(row['label']) },
            { key: 'calls', header: 'Calls', numeric: true, cell: (row) => Number(row['calls']) },
            { key: 'usd', header: 'Est.', numeric: true, cell: (row) => money(Number(row['est_usd'])) },
            { key: 'note', header: 'Note', cell: (row) => String(row['note'] ?? '') },
          ]}
        />
      </Card>
    </>
  );
}

function StandupView({ report }: { report: Report }) {
  const highlights = lines(report, 'highlights');
  const inFlight = lines(report, 'in_flight');
  const attention = lines(report, 'attention_items');
  const coverage = lines(report, 'coverage_notes');
  return (
    <>
      <Card title={text(report, 'digest_date') || 'Digest'}>
        <StatGrid>
          <StatTile label="Sessions worked" value={String(num(report, 'sessions_worked'))} />
          <StatTile label="Estimated spend" value={money(num(report, 'total_cost_usd'))} />
        </StatGrid>
        {text(report, 'narrative') && <p>{text(report, 'narrative')}</p>}
        {coverage.length > 0 && <NoticeBlock title="What this could not see" items={coverage} />}
      </Card>
      {highlights.length > 0 && (
        <Card title="Highlights">
          <ul class="review-list">
            {highlights.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </Card>
      )}
      {inFlight.length > 0 && (
        <Card title="Still in flight">
          <ul class="review-list">
            {inFlight.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </Card>
      )}
      {attention.length > 0 && <NoticeBlock title="Needs a person" items={attention} />}
      <Card title="Repo activity">
        <DataTable
          rows={rows(report, 'repo_activity')}
          empty="No tracked repository activity in the window."
          columns={[
            { key: 'repo', header: 'Repo', cell: (row) => String(row['repo']) },
            { key: 'kind', header: 'Kind', cell: (row) => String(row['kind']) },
            { key: 'title', header: 'What', cell: (row) => String(row['title']) },
            { key: 'status', header: 'Status', cell: (row) => String(row['status'] ?? '') },
          ]}
        />
      </Card>
    </>
  );
}

function SecurityView({ report }: { report: Report }) {
  const posture = text(report, 'posture');
  return (
    <>
      <Card
        title={`Posture: ${posture || 'unknown'}`}
        actions={<Lozenge category={posture === 'good' ? 'done' : 'blocked'}>{posture || 'unknown'}</Lozenge>}
      >
        <StatGrid>
          <StatTile label="Sessions scanned" value={String(num(report, 'sessions_scanned'))} />
          <StatTile label="Files scanned" value={String(num(report, 'files_scanned'))} />
          <StatTile label="Secrets found" value={String(num(report, 'secrets_found'))} />
        </StatGrid>
        {text(report, 'summary') && <p>{text(report, 'summary')}</p>}
      </Card>
      <Card title="Findings">
        <DataTable
          rows={rows(report, 'findings')}
          empty="Nothing flagged."
          columns={[
            {
              key: 'severity',
              header: 'Severity',
              cell: (row) => (
                <Lozenge category={severityCategory(String(row['severity']))} small>
                  {String(row['severity'])}
                </Lozenge>
              ),
            },
            { key: 'title', header: 'Finding', cell: (row) => String(row['title']) },
            { key: 'location', header: 'Where', cell: (row) => String(row['location'] ?? '') },
            { key: 'remediation', header: 'Fix', cell: (row) => String(row['remediation'] ?? '') },
          ]}
        />
      </Card>
      <Card title="MCP servers">
        <DataTable
          rows={rows(report, 'mcp_servers')}
          rowKey={(row) => `${String(row['scope'])}/${String(row['name'])}`}
          empty="No MCP servers configured."
          columns={[
            { key: 'name', header: 'Name', cell: (row) => String(row['name']) },
            { key: 'scope', header: 'Scope', cell: (row) => String(row['scope']) },
            { key: 'transport', header: 'Transport', cell: (row) => String(row['transport']) },
            { key: 'flags', header: 'Flags', cell: (row) => (row['flags'] as string[]).join(', ') },
          ]}
        />
      </Card>
    </>
  );
}
