// New delivery report — pick a period, confirm the sources, then the run.
//
// Which extra step a period earns, and what a set of checked sprints makes as a
// window, are asked of the backend: the terminal walks the same flow, and a
// second copy of either rule is a second thing to drift.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import {
  type ModeRunState,
  type ReportingOptions,
  type SprintList,
  cancelModeRun,
  emptyModeRun,
  loadReportingOptions,
  loadSprints,
  reduceModeRun,
  resolveWindow,
  runReport,
} from '../modes';

const QUARTER = 'quarter';
const WINDOW = 'window';

export function ReportingSetup() {
  const [options, setOptions] = useState<ReportingOptions | null>(null);
  const [period, setPeriod] = useState('');
  const [theme, setTheme] = useState('midnight');
  const [sources, setSources] = useState<Record<string, string[]> | null>(null);
  const [sprints, setSprints] = useState<SprintList | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [range, setRange] = useState({ start: '', end: '' });
  const [run, setRun] = useState<ModeRunState>(emptyModeRun());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    loadReportingOptions().then(
      (opts) => {
        setOptions(opts);
        setPeriod(opts.periods[0]?.key ?? '');
        setSources({ ...opts.sources.grid });
        setRange({ start: opts.default_window.start, end: opts.default_window.end });
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  // The quarter's sprint list is loaded only when the quarter is chosen —
  // it costs a tracker round-trip.
  useEffect(() => {
    if (period !== QUARTER || sprints) return;
    loadSprints('').then(
      (list) => {
        setSprints(list);
        setChecked(list.checked);
      },
      (e: Error) => setError(e.message),
    );
  }, [period, sprints]);

  if (error && !options) return <NoticeBlock title="Could not open reporting" items={[error]} />;
  if (!options || !sources) return <p>Loading…</p>;

  const toggleSource = (component: string, name: string) => {
    const current = sources[component] ?? [];
    setSources({
      ...sources,
      [component]: current.includes(name) ? current.filter((s) => s !== name) : [...current, name],
    });
  };

  const toggleSprint = (index: number) =>
    setChecked(checked.includes(index) ? checked.filter((i) => i !== index) : [...checked, index]);

  async function start() {
    if (busy) return;
    setBusy(true);
    setError('');
    let state = emptyModeRun();
    setRun(state);
    try {
      const body: Record<string, unknown> = { period, theme, sources };
      if (period === QUARTER && sprints) {
        // Empty checks and no sprint list are different answers: with no list
        // at all the backend already handed back the calendar-quarter window.
        const resolved = sprints.sprints.length
          ? await resolveWindow(sprints.sprints, checked)
          : sprints.fallback;
        Object.assign(body, resolved);
      }
      if (period === WINDOW) {
        body.window_start = range.start;
        body.window_end = range.end;
      }
      await runReport(body, (line) => {
        state = reduceModeRun(state, line);
        setRun(state);
      });
      if (state.done) window.location.hash = '#/humans/reporting';
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  const canRun = period !== WINDOW || Boolean(range.start && range.end);

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">New report</h1>
          <p class="dash-sub">{options.sources.summary}</p>
        </div>
        <div class="dash-actions">
          <a class="button" href="#/humans/reporting">
            Back
          </a>
        </div>
      </header>

      {error && <NoticeBlock title="Could not generate the report" items={[error]} />}

      <Card title="Period">
        <div class="chip-row">
          {options.periods.map((option) => (
            <label key={option.key} class="check-row">
              <input
                type="radio"
                name="period"
                checked={period === option.key}
                onChange={() => setPeriod(option.key)}
              />
              <span>
                <strong>{option.label}</strong>
                <span class="dash-note">{option.description}</span>
              </span>
            </label>
          ))}
        </div>
      </Card>

      {period === WINDOW && (
        <Card title="Custom range">
          <div class="field-row">
            <label>
              Start
              <input
                type="date"
                value={range.start}
                onInput={(e) => setRange({ ...range, start: (e.target as HTMLInputElement).value })}
              />
            </label>
            <label>
              End
              <input
                type="date"
                value={range.end}
                onInput={(e) => setRange({ ...range, end: (e.target as HTMLInputElement).value })}
              />
            </label>
          </div>
        </Card>
      )}

      {period === QUARTER && sprints && sprints.sprints.length > 0 && (
        <Card title="Which sprints make up the quarter">
          <div class="chip-row">
            {sprints.sprints.map((sprint, index) => (
              <label key={sprint.name} class="check-row">
                <input type="checkbox" checked={checked.includes(index)} onChange={() => toggleSprint(index)} />
                <span>
                  <strong>{sprint.name}</strong>
                  <span class="dash-note">
                    {sprint.start_date} → {sprint.end_date}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </Card>
      )}

      {period === QUARTER && sprints && sprints.sprints.length === 0 && (
        <NoticeBlock
          title="No sprint list available"
          items={['The report will cover the calendar quarter instead.']}
        />
      )}

      {options.sources.step_applies && (
        <Card title="Sources">
          {Object.entries(options.sources.grid).map(([component, available]) => (
            <div key={component} class="field-row">
              <span>
                <strong>{component}</strong>
                <span class="dash-note">{options.sources.descriptions[component]}</span>
              </span>
              <div class="chip-row">
                {available.map((name) => (
                  <label key={name} class="check-row">
                    <input
                      type="checkbox"
                      checked={(sources[component] ?? []).includes(name)}
                      onChange={() => toggleSource(component, name)}
                    />
                    <span>{options.sources.titles[name] ?? name}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </Card>
      )}

      <Card title="Presentation theme">
        <div class="chip-row">
          {options.themes.map((name) => (
            <button
              key={name}
              type="button"
              class={theme === name ? 'primary' : ''}
              onClick={() => setTheme(name)}
              style={{ borderColor: options.palettes[name]?.accent ?? undefined }}
            >
              {name}
            </button>
          ))}
        </div>
      </Card>

      <div class="dash-actions">
        <button type="button" class="primary" disabled={busy || !canRun} onClick={() => void start()}>
          {busy ? 'Generating…' : 'Generate report'}
        </button>
        {busy && run.opId && (
          <button type="button" onClick={() => void cancelModeRun(run.opId)}>
            Stop
          </button>
        )}
      </div>

      {run.phases.length > 0 && (
        <Card title="Progress">
          <ol class="phase-list">
            {run.phases.map((phase, i) => (
              <li key={`${phase}-${i}`}>{phase}</li>
            ))}
          </ol>
        </Card>
      )}

      {run.cancelled && <NoticeBlock title="Stopped" items={['Nothing was saved.']} />}
      {run.error && <NoticeBlock title="The run failed" items={[run.error]} />}
    </div>
  );
}
