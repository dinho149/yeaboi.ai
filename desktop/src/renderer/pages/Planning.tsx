// Planning — the intake tiles. Describe the project, optionally size it, and
// the conversation opens on the answer (the TUI's greeting exchange, as a form
// rather than three turns of chat).

import { Duck } from '@design/primitives/Duck';
import { useState } from 'react';
import { createChat } from '../chat';

const SIZES = [
  { key: '', label: 'Let yeaboi decide', hint: 'the description is classified for you' },
  { key: 'small_project', label: 'Small', hint: 'a ticket or two, one quick sprint' },
  { key: 'smart', label: 'Large', hint: 'epics and multiple sprints' },
];

export function Planning() {
  const [description, setDescription] = useState('');
  const [size, setSize] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function start() {
    if (!description.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      const view = await createChat(description.trim(), size);
      window.location.hash = `#/humans/planning/chat?id=${encodeURIComponent(view.project_id)}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div class="intake">
      <div class="intake-head">
        <Duck state="idle" size={56} />
        <div>
          <h1 class="page-title">Plan a project</h1>
          <p class="intake-lead">
            Tell me what you're building and why. I'll ask the rest, then draw up epics, stories, tasks and sprints.
          </p>
        </div>
      </div>

      <textarea
        class="intake-box"
        rows={6}
        placeholder="We're building a mobile app for restaurant reservations…"
        value={description}
        disabled={busy}
        onInput={(e) => setDescription((e.target as HTMLTextAreaElement).value)}
      />

      <div class="intake-sizes">
        {SIZES.map((option) => (
          <button
            key={option.key}
            type="button"
            class={option.key === size ? 'size-card active' : 'size-card'}
            onClick={() => setSize(option.key)}
          >
            <strong>{option.label}</strong>
            <span>{option.hint}</span>
          </button>
        ))}
      </div>

      {error && <p class="intake-error">{error}</p>}

      <div class="intake-actions">
        <button type="button" class="primary" disabled={busy || !description.trim()} onClick={() => void start()}>
          {busy ? 'Opening…' : 'Start planning'}
        </button>
        <a href="#/humans/planning/sessions">Saved plans</a>
      </div>
    </div>
  );
}
