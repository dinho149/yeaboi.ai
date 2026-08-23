// The poker setup wizard: source → scope → sprint → types → fetch → table.
//
// Which of those four steps this configuration actually asks comes from
// /api/poker/options and the per-step probes, never from a rule kept here — the
// terminal and the desktop must walk the same wizard, and a demo session skips
// everything after the source for a reason the backend owns.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import {
  type PickOption,
  type PokerOptions,
  fetchPokerTickets,
  loadPokerOptions,
  loadPokerSprints,
  loadPokerTypes,
  startPokerBoard,
} from '../boards';

interface Ticket {
  key?: string;
  id?: string;
  title?: string;
  summary?: string;
}

export function PokerSetup() {
  const [options, setOptions] = useState<PokerOptions | null>(null);
  const [error, setError] = useState('');
  const [source, setSource] = useState('');
  const [scope, setScope] = useState('');
  const [sprints, setSprints] = useState<Record<string, unknown>[]>([]);
  const [sprintOptions, setSprintOptions] = useState<PickOption[]>([]);
  const [sprintIndex, setSprintIndex] = useState(0);
  const [types, setTypes] = useState<PickOption[]>([]);
  const [typeHint, setTypeHint] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [scopeLabel, setScopeLabel] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadPokerOptions().then(setOptions, (e: Error) => setError(e.message));
  }, []);

  // The demo source answers only the first question; a real tracker earns the
  // rest. `steps` is the backend's list, so this reads it rather than deciding.
  const steps = options?.steps ?? [];
  const asksScope = Boolean(source) && source !== 'demo' && steps.includes('scope');
  const asksSprint = asksScope && scope === 'sprint';
  const asksTypes = Boolean(source) && source !== 'demo' && steps.includes('types');
  const ready = Boolean(source) && (source === 'demo' || (Boolean(scope) && (!asksSprint || sprints.length > 0)));

  async function pickSource(key: string) {
    setSource(key);
    setScope('');
    setSprints([]);
    setTickets(null);
    if (key === 'demo') return;
    try {
      const body = await loadPokerTypes(key);
      setTypes(body.types);
      setTypeHint(body.hint);
      setChecked(body.types.filter((type) => type.checked).map((type) => type.key));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function pickScope(key: string) {
    setScope(key);
    if (key !== 'sprint') return;
    try {
      const body = await loadPokerSprints(source);
      setSprints(body.sprints);
      setSprintOptions(body.options);
      setSprintIndex(body.default_index);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function fetchScope() {
    setBusy(true);
    setError('');
    try {
      const body = await fetchPokerTickets({
        source,
        scope,
        sprint: asksSprint ? (sprints[sprintIndex] ?? null) : null,
        include_types: asksTypes ? checked : null,
      });
      setScopeLabel(body.scope_label);
      setTickets(body.tickets as Ticket[]);
      // An empty answer is a configuration story, not an error — and the
      // backend wrote the sentence that says which one.
      if (body.message) setError(body.message);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  async function deal() {
    if (!tickets?.length) return;
    setBusy(true);
    try {
      const board = await startPokerBoard({ source, scope_label: scopeLabel, tickets });
      window.location.hash = `#/humans/poker/board?id=${encodeURIComponent(board.board_id)}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (!options) return error ? <NoticeBlock title="Could not load the wizard" items={[error]} /> : <p>Loading…</p>;

  return (
    <div class="dash">
      <h1 class="page-title">New poker session</h1>
      {error && <NoticeBlock title="Heads up" items={[error]} />}

      <Card title={options.titles.source ?? 'Where do the tickets come from?'}>
        {options.source_hint && <p class="dash-note">{options.source_hint}</p>}
        <ul class="pick-list">
          {options.sources.map((option) => (
            <li key={option.key}>
              <button
                type="button"
                class={source === option.key ? 'primary' : undefined}
                onClick={() => void pickSource(option.key)}
              >
                {option.label}
              </button>
              <span class="dest-note">{option.sub}</span>
            </li>
          ))}
        </ul>
      </Card>

      {asksScope && (
        <Card title={options.titles.scope ?? 'Which tickets?'}>
          <ul class="pick-list">
            {options.scopes.map((option) => (
              <li key={option.key}>
                <button
                  type="button"
                  class={scope === option.key ? 'primary' : undefined}
                  onClick={() => void pickScope(option.key)}
                >
                  {option.label}
                </button>
                <span class="dest-note">{option.sub}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {asksSprint && (
        <Card title={options.titles.sprint ?? 'Which sprint?'}>
          {sprintOptions.length === 0 ? (
            <p class="dash-note">
              No sprints found — check the board&apos;s credentials, or estimate the backlog instead.
            </p>
          ) : (
            <select value={String(sprintIndex)} onChange={(e) => setSprintIndex(Number((e.target as HTMLSelectElement).value))}>
              {sprintOptions.map((option, index) => (
                <option key={option.key} value={String(index)}>
                  {option.label}
                  {option.sub ? ` · ${option.sub}` : ''}
                </option>
              ))}
            </select>
          )}
        </Card>
      )}

      {asksTypes && (
        <Card title={options.titles.types ?? 'Which ticket types?'}>
          <p class="dash-note">{typeHint}</p>
          {types.map((type) => (
            <label key={type.key} class="check">
              <input
                type="checkbox"
                checked={checked.includes(type.key)}
                onChange={() =>
                  setChecked(
                    checked.includes(type.key)
                      ? checked.filter((key) => key !== type.key)
                      : [...checked, type.key],
                  )
                }
              />
              <span>
                {type.label} <span class="dest-note">{type.sub}</span>
              </span>
            </label>
          ))}
        </Card>
      )}

      {ready && (
        <div class="dash-actions">
          <button type="button" disabled={busy} onClick={() => void fetchScope()}>
            {busy ? 'Fetching…' : 'Fetch tickets'}
          </button>
        </div>
      )}

      {tickets && tickets.length > 0 && (
        <Card title={`${tickets.length} tickets — ${scopeLabel}`}>
          <ul class="retro-cards">
            {tickets.slice(0, 12).map((ticket, index) => (
              <li key={ticket.key ?? ticket.id ?? index}>
                <strong>{ticket.key ?? ticket.id}</strong> {ticket.title ?? ticket.summary}
              </li>
            ))}
          </ul>
          {tickets.length > 12 && <p class="dash-note">…and {tickets.length - 12} more.</p>}
          <button type="button" class="primary" disabled={busy} onClick={() => void deal()}>
            {busy ? 'Dealing…' : 'Open the table'}
          </button>
        </Card>
      )}
    </div>
  );
}
