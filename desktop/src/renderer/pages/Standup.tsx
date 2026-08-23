// Daily Standup — the dashboard, card for card with the terminal's.
//
// The card list comes from the backend (standup/dashboard.py decides which
// cards a report earns), so this file draws cards rather than choosing them.

import { Card, Lozenge, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { quip } from '../ambience';
import { maskText } from '../boards';
import { ArtifactEditor } from '../components/ArtifactEditor';
import { ResultActions } from '../components/ResultActions';
import {
  type DashboardCard,
  type MemberUpdate,
  type RunLine,
  type StandupDashboard,
  deleteRun,
  emptyRun,
  loadStandup,
  ratePractice,
  reduceRun,
  runStandup,
} from '../dashboards';

const CONFIDENCE_TONE: Record<string, 'done' | 'inprogress' | 'blocked'> = {
  'On track': 'done',
  'At risk': 'inprogress',
  Behind: 'blocked',
};

export function Standup() {
  const [data, setData] = useState<StandupDashboard | null>(null);
  const [error, setError] = useState('');
  const [run, setRun] = useState(emptyRun());
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState('summary');
  const [runId, setRunId] = useState(0);
  const [showRuns, setShowRuns] = useState(false);
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');
  const [showEditor, setShowEditor] = useState(false);

  useEffect(() => {
    void refresh(runId);
  }, [runId]);

  async function refresh(id = runId) {
    try {
      setData(await loadStandup('', id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function drop(id: number) {
    await deleteRun(id);
    // Deleting the run that is open puts the page back on the latest one.
    if (id === runId) setRunId(0);
    else await refresh();
  }

  async function generate(deliver: boolean) {
    if (!data?.session_id || busy) return;
    setBusy(true);
    setError('');
    let state = emptyRun();
    setRun(state);
    try {
      await runStandup(data.session_id, deliver, (line: RunLine) => {
        state = reduceRun(state, line);
        setRun(state);
      });
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
    if (state.error) setError(state.error);
    // The report is re-read rather than taken off the stream: the run also
    // wrote the review, the nudge and the history this page shows.
    else {
      quip('standup_done');
      await refresh();
    }
  }

  if (error && !data) return <NoticeBlock title="Could not load the standup" items={[error]} />;
  if (!data) return <p>Loading…</p>;

  const report = data.report;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Daily Standup</h1>
          <p class="dash-sub">
            {data.session_name || 'No project yet'}
            {report ? ` · ${report.date}` : ' · nothing generated yet'}
          </p>
        </div>
        <div class="dash-actions">
          <button type="button" class="primary" disabled={busy || !data.session_id} onClick={() => void generate(false)}>
            {busy ? 'Generating…' : 'Generate'}
          </button>
          <button type="button" disabled={busy || !report} onClick={() => void generate(true)}>
            Generate &amp; deliver
          </button>
          <button type="button" onClick={() => setShowRuns(!showRuns)}>
            {showRuns ? 'Hide past runs' : `Past runs (${data.history.length})`}
          </button>
          <a href="#/humans/standup/setup">Setup</a>
          <a href="#/humans/standup/schedule">Schedule</a>
          <a href="#/humans/standup/review">Transcript review</a>
        </div>
      </header>

      {report && (
        <>
          <ResultActions
            refer={{ kind: 'standup', session_id: data.session_id, run_id: runId }}
            mode="standup"
            anonNote={anonNote}
            onAnonymize={(replacements, note) => {
              setMask(replacements);
              setAnonNote(note);
            }}
          />
          <div class="dash-actions">
            <button type="button" onClick={() => setShowEditor(!showEditor)}>
              {showEditor ? 'Hide corrections' : 'Corrections'}
            </button>
          </div>
          {showEditor && (
            <ArtifactEditor
              refer={{ kind: 'standup', session_id: data.session_id, run_id: runId }}
              onApplied={() => void refresh()}
            />
          )}
        </>
      )}

      {showRuns && (
        <Card title="Past runs">
          <ul class="member-list">
            {data.history.map((entry) => (
              <li key={entry.id}>
                <span class={entry.id === runId || (!runId && entry === data.history[0]) ? 'dot active' : 'dot'} />
                <button type="button" class="rail-tab" onClick={() => setRunId(entry.id)}>
                  {entry.standup_date} · day {entry.sprint_day} · {entry.confidence_pct}%
                </button>
                <span>
                  {entry.status}
                  <button type="button" onClick={() => void drop(entry.id)}>
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
          {!data.history.length && <p>No runs yet.</p>}
          {runId ? (
            <p class="dash-note">
              Showing a past run.{' '}
              <button type="button" class="rail-tab" onClick={() => setRunId(0)}>
                Back to the latest
              </button>
            </p>
          ) : null}
        </Card>
      )}

      {busy && (
        <Card title="Working">
          <ol class="phase-list">
            {run.phases.map((phase, index) => (
              <li key={`${index}-${phase}`}>{phase}</li>
            ))}
            {!run.phases.length && <li>Starting…</li>}
          </ol>
          <p class="dash-note">
            <Duck state="idle" size={20} /> A standup run cannot be stopped part-way — it finishes or it fails.
          </p>
        </Card>
      )}

      {error && <NoticeBlock title="That run did not finish" items={[error]} />}

      {report && (
        <StatGrid>
          <StatTile label="Sprint" value={report.sprint_name || '—'} />
          <StatTile
            label="Sprint day"
            value={report.sprint_total_days ? `${report.sprint_day} of ${report.sprint_total_days}` : '—'}
          />
          <StatTile label="Confidence" value={report.confidence_label || '—'} />
          <StatTile label="Updates" value={String(report.member_updates.length)} />
        </StatGrid>
      )}

      <div class="card-rail">
        {data.cards.map((card) => (
          <button
            key={card.key}
            type="button"
            class={card.key === open ? 'rail-tab active' : 'rail-tab'}
            onClick={() => setOpen(card.key)}
          >
            {card.title}
          </button>
        ))}
      </div>

      <CardBody
        card={data.cards.find((c) => c.key === open) ?? data.cards[0]!}
        data={data}
        mask={mask}
        onRated={() => void refresh()}
      />
    </div>
  );
}

function CardBody({
  card,
  data,
  mask,
  onRated,
}: {
  card: DashboardCard;
  data: StandupDashboard;
  /** Non-empty while the page is masked — the same data, drawn differently. */
  mask: [string, string][];
  onRated: () => void;
}) {
  const report = data.report;
  if (card.member) {
    const member = report?.member_updates.find((m) => m.name === card.member);
    return member ? (
      <MemberCard
        member={member}
        sessionId={data.session_id}
        active={data.active.includes(member.name)}
        mask={mask}
        onRated={onRated}
      />
    ) : null;
  }
  switch (card.key) {
    case 'summary':
      return (
        <Card title="Team Summary">
          <p>{maskText(report?.team_summary ?? '', mask) || 'No summary yet.'}</p>
          {report?.confidence_label && (
            <p class="dash-note">
              <Lozenge category={CONFIDENCE_TONE[report.confidence_label] ?? 'todo'} small>
                {report.confidence_label} · {report.confidence_pct}%
              </Lozenge>{' '}
              {report.confidence_rationale}
            </p>
          )}
        </Card>
      );
    case 'my_update': {
      const mine = report?.member_updates.find((m) => m.name === data.my_name);
      return mine ? (
        <MemberCard
          member={mine}
          sessionId={data.session_id}
          active={data.active.includes(mine.name)}
          mask={mask}
          onRated={onRated}
        />
      ) : (
        <Card title="My Update">
          <p>No update yet — Generate asks for it.</p>
        </Card>
      );
    }
    case 'team':
      return (
        <Card title="Team">
          <ul class="member-list">
            {(report?.member_updates ?? [])
              .filter((m) => m.name !== data.my_name)
              .map((m) => (
                <li key={m.name}>
                  <span class={data.active.includes(m.name) ? 'dot active' : 'dot'} />
                  <strong>{m.name}</strong>
                  <span>{m.summary || 'No activity detected.'}</span>
                </li>
              ))}
          </ul>
        </Card>
      );
    case 'conflicts':
      return (
        <Card title="Conflicts">
          <ul>
            {(report?.conflicts ?? []).map((conflict) => (
              <li key={conflict.entity_id}>
                <strong>{conflict.entity_id}</strong> — {maskText(conflict.summary, mask)}
              </li>
            ))}
          </ul>
        </Card>
      );
    case 'activity':
      return (
        <Card title="Activity">
          <StatGrid>
            {(report?.activity_counts ?? []).map(([source, count]) => (
              <StatTile key={source} label={source} value={String(count)} />
            ))}
          </StatGrid>
          {!report?.activity_counts.length && <p>No sources reported activity.</p>}
        </Card>
      );
    case 'gaps':
      return (
        <Card title="Transcript Review" actions={<a href="#/humans/standup/review">Open</a>}>
          {data.nudge?.missed_dates.length ? (
            <p>
              {data.nudge.missed_dates.length} standup{data.nudge.missed_dates.length === 1 ? '' : 's'} went unchecked
              — oldest {data.nudge.missed_dates[data.nudge.missed_dates.length - 1]}.
            </p>
          ) : null}
          {data.review ? (
            <p>
              {data.review.gaps.length} gap{data.review.gaps.length === 1 ? '' : 's'} found ·{' '}
              {data.gap_issues.filter((entry) => entry.issue_number).length} filed.
            </p>
          ) : (
            <p>Not reviewed yet.</p>
          )}
        </Card>
      );
    case 'schedule': {
      const config = (data.config ?? {}) as { enabled?: boolean; time?: string; weekdays?: string };
      return (
        <Card title="Schedule" actions={<a href="#/humans/standup/schedule">Set up</a>}>
          {data.config ? (
            <p>
              {config.enabled ? 'Enabled' : 'Off'} · {config.time ?? '—'} · {config.weekdays ?? '—'}
              {data.schedule.installed ? ` · installed (${data.schedule.platform ?? 'os'})` : ' · no OS job'}
            </p>
          ) : (
            <p>Not configured.</p>
          )}
        </Card>
      );
    }
    case 'notices':
      return (
        <Card title="Notices">
          <NoticeBlock title="What could not be read" items={report?.warnings ?? []} />
        </Card>
      );
    default:
      return null;
  }
}

function MemberCard({
  member,
  sessionId,
  active,
  mask,
  onRated,
}: {
  member: MemberUpdate;
  sessionId: string;
  active: boolean;
  mask: [string, string][];
  onRated: () => void;
}) {
  const [rated, setRated] = useState<Record<string, string>>({});

  async function rate(rule: string, verdict: string) {
    setRated((prior) => ({ ...prior, [rule]: verdict }));
    await ratePractice(sessionId, member.name, rule, verdict);
    onRated();
  }

  return (
    <Card
      title={maskText(member.name, mask)}
      actions={<Lozenge category={active ? 'done' : 'todo'} small>{active ? 'active' : 'quiet'}</Lozenge>}
    >
      <p>{maskText(member.summary, mask) || 'No activity detected.'}</p>
      {member.blockers && <NoticeBlock title="Blocked" items={[maskText(member.blockers, mask)]} />}
      {member.self_report && <p class="dash-note">✍ {maskText(member.self_report, mask)}</p>}
      {member.links?.length ? (
        <ul class="evidence">
          {member.links.map(([label, url]) => (
            <li key={url}>
              <a href={url} target="_blank" rel="noreferrer">
                {label || url}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      {member.practices?.length ? (
        <div class="practices">
          <h3>Practices</h3>
          {member.practices.map((signal) => (
            <div key={signal.rule} class="practice-row">
              <span>{signal.detail}</span>
              {rated[signal.rule] ? (
                <span class="dash-note">Thanks — remembered.</span>
              ) : (
                <span class="practice-vote">
                  <button type="button" onClick={() => void rate(signal.rule, 'confirmed')}>
                    Fair
                  </button>
                  <button type="button" onClick={() => void rate(signal.rule, 'excused')}>
                    Not this one
                  </button>
                </span>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
