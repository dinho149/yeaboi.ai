// Ceremonies — the clock every mode can run on.
//
// Two things this page is careful about, both inherited from the terminal one.
// Drift is shown first: the store says what is declared, the OS says what will
// fire, and nothing else in the app would ever mention the gap. And Run now is
// not a scheduled fire — the guards that answer "is this too late to be
// useful" belong to an unattended run, not to somebody pressing a button.

import { Card, Lozenge, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import {
  type CeremoniesPage,
  type CeremonyRow,
  declareCeremony,
  loadCeremonies,
  removeCeremony,
  runCeremony,
  setCeremonyEnabled,
} from '../ops';

function outcomeCategory(row: CeremonyRow): 'todo' | 'inprogress' | 'done' | 'blocked' {
  if (!row.last_run) return 'todo';
  if (row.last_run.outcome === 'ok') return 'done';
  if (row.last_run.outcome === 'failed') return 'blocked';
  return 'inprogress';
}

export function Ceremonies() {
  const [page, setPage] = useState<CeremoniesPage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [running, setRunning] = useState('');
  const [phases, setPhases] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);

  function refresh() {
    return loadCeremonies().then(setPage, (e: Error) => setError(e.message));
  }

  useEffect(() => {
    void refresh();
  }, []);

  if (error && !page) return <NoticeBlock title="Could not read the schedule" items={[error]} />;
  if (!page) return <p>Loading…</p>;

  async function act(work: () => Promise<unknown>, done: string) {
    setError('');
    try {
      await work();
      setNotice(done);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function fire(name: string) {
    setRunning(name);
    setPhases([]);
    setNotice('');
    setError('');
    try {
      await runCeremony(name, (line) => {
        const row = (line ?? {}) as Record<string, unknown>;
        if (row.type === 'progress') setPhases((seen) => [...seen, String(row.phase ?? '')]);
        if (row.type === 'done') setNotice(String(row.summary ?? ''));
        if (row.type === 'error') setError(String(row.message ?? 'The run stopped.'));
      });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning('');
    }
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Ceremonies</h1>
          <p class="dash-sub">The clock other modes run on — declared once, fired by your machine.</p>
        </div>
        <div class="dash-actions">
          <a class="button" href="#/ceremonies/slack">
            Slack
          </a>
          <button type="button" onClick={() => setAdding((open) => !open)}>
            {adding ? 'Cancel' : 'Declare one'}
          </button>
        </div>
      </header>

      {error && <NoticeBlock title="That did not work" items={[error]} />}
      {notice && <NoticeBlock title="Done" items={[notice]} />}
      {page.drift.length > 0 && (
        <NoticeBlock title="Your machine and this list disagree" items={page.drift} />
      )}

      {adding && (
        <DeclareForm
          page={page}
          onDone={(message) => {
            setAdding(false);
            setNotice(message);
            void refresh();
          }}
          onError={setError}
        />
      )}

      {page.ceremonies.length === 0 ? (
        <Card title="Nothing scheduled">
          <p>
            <Duck state="idle" size={28} /> {page.empty_message}
          </p>
          <p class="dash-note">{page.add_hint}</p>
        </Card>
      ) : (
        page.ceremonies.map((row) => (
          <Card
            key={row.name}
            title={row.name}
            actions={<Lozenge category={outcomeCategory(row)}>{row.last_run?.outcome ?? 'never run'}</Lozenge>}
          >
            <StatGrid>
              <StatTile label="Runs" value={row.next_fire} />
              <StatTile label="Mode" value={row.mode} />
              <StatTile label="Delivers to" value={row.channels.join(', ') || 'nowhere'} />
              <StatTile label="This month" value={`$${row.month_spend_usd.toFixed(2)}`} />
            </StatGrid>
            {row.last_run?.detail && <p class="dash-note">{row.last_run.detail}</p>}
            {row.last_run?.error && <NoticeBlock title="Last run failed" items={[row.last_run.error]} />}
            {running === row.name && (
              <ul class="phase-list">
                {phases.map((phase, index) => (
                  <li key={`${phase}-${index}`}>{phase}</li>
                ))}
              </ul>
            )}
            <div class="dash-actions">
              <button
                type="button"
                class="primary"
                disabled={Boolean(running)}
                onClick={() => void fire(row.name)}
              >
                {running === row.name ? 'Running…' : 'Run now'}
              </button>
              <button
                type="button"
                disabled={Boolean(running)}
                onClick={() =>
                  void act(
                    () => setCeremonyEnabled(row.name, !row.enabled),
                    `${row.name} ${row.enabled ? 'paused' : 'resumed'}.`,
                  )
                }
              >
                {row.enabled ? 'Pause' : 'Resume'}
              </button>
              <button
                type="button"
                disabled={Boolean(running)}
                onClick={() => void act(() => removeCeremony(row.name), `${row.name} removed.`)}
              >
                Remove
              </button>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}

function DeclareForm({
  page,
  onDone,
  onError,
}: {
  page: CeremoniesPage;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const first = page.modes[0];
  const [mode, setMode] = useState(first?.key ?? '');
  const [name, setName] = useState('');
  const [at, setAt] = useState(first?.default_at ?? '09:00');
  const [weekdays, setWeekdays] = useState(first?.default_weekdays ?? '1-5');
  const [channels, setChannels] = useState<string[]>(['terminal']);
  const [busy, setBusy] = useState(false);
  const picked = page.modes.find((option) => option.key === mode);

  async function submit() {
    setBusy(true);
    try {
      const declared = await declareCeremony({ name, mode, at, weekdays, channels });
      // The equivalent terminal command comes back with it, so a surface that
      // installed a recurring job can also say exactly what it installed.
      onDone(`${declared.ceremony.name} — ${declared.cadence}. ${declared.command}`);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Declare a ceremony">
      <div class="field-row">
        <label for="cer-mode">Mode</label>
        <select
          id="cer-mode"
          value={mode}
          onChange={(e) => {
            const key = (e.target as HTMLSelectElement).value;
            setMode(key);
            const option = page.modes.find((row) => row.key === key);
            if (option) {
              setAt(option.default_at);
              setWeekdays(option.default_weekdays);
            }
          }}
        >
          {page.modes.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      {picked && (
        <p class="dash-note">
          {picked.blurb} · about ${picked.est_cost_usd.toFixed(2)} a run
        </p>
      )}
      <div class="field-row">
        <label for="cer-name">Name</label>
        <input
          id="cer-name"
          type="text"
          value={name}
          placeholder="morning-standup"
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
      </div>
      <div class="field-row">
        <label for="cer-at">At</label>
        <input id="cer-at" type="text" value={at} onInput={(e) => setAt((e.target as HTMLInputElement).value)} />
      </div>
      <div class="field-row">
        <label for="cer-days">Days</label>
        <input
          id="cer-days"
          type="text"
          value={weekdays}
          onInput={(e) => setWeekdays((e.target as HTMLInputElement).value)}
        />
      </div>
      <div class="field-row">
        <label>Deliver to</label>
        <div class="chip-row">
          {page.channels.map((channel) => (
            <label key={channel} class="check-row">
              <input
                type="checkbox"
                checked={channels.includes(channel)}
                onChange={() =>
                  setChannels((chosen) =>
                    chosen.includes(channel) ? chosen.filter((c) => c !== channel) : [...chosen, channel],
                  )
                }
              />
              <span>{channel}</span>
            </label>
          ))}
        </div>
      </div>
      <div class="dash-actions">
        <button type="button" class="primary" disabled={busy || !name || !mode} onClick={() => void submit()}>
          {busy ? 'Installing…' : 'Declare and install'}
        </button>
      </div>
    </Card>
  );
}
