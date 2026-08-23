// The deck style — what the slides look like, saved once and used by every
// report. The field list and its choices come from the backend, so the two
// surfaces edit the same vocabulary rather than two copies of it.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import {
  type DeckStyle,
  type ReportingOptions,
  type StyleField,
  loadReportingOptions,
  resetDeckStyle,
  saveDeckStyle,
} from '../modes';

export function ReportingStyle() {
  const [options, setOptions] = useState<ReportingOptions | null>(null);
  const [style, setStyle] = useState<DeckStyle | null>(null);
  const [summary, setSummary] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    loadReportingOptions().then(
      (opts) => {
        setOptions(opts);
        setStyle({ ...opts.style });
        setSummary(opts.style_summary);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error && !options) return <NoticeBlock title="Could not open the deck style" items={[error]} />;
  if (!options || !style) return <p>Loading…</p>;

  const set = (key: string, value: string | number | boolean) => {
    setStyle({ ...style, [key]: value });
    setMessage('');
  };

  const choicesFor = (key: string): (string | number)[] => {
    const c = options.style_choices;
    if (key === 'font_family') return c.fonts;
    if (key === 'font_scale') return c.font_scales;
    if (key === 'layout') return c.layouts;
    if (key === 'content_fit') return c.content_fits;
    if (key === 'max_bullets') return c.max_bullets;
    return [];
  };

  const label = (key: string, value: string | number): string =>
    key === 'content_fit' ? (options.style_choices.content_fit_labels[String(value)] ?? String(value)) : String(value);

  async function save() {
    try {
      const saved = await saveDeckStyle(style!);
      setStyle(saved.style);
      setSummary(saved.style_summary);
      setMessage('Saved.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function reset() {
    try {
      const saved = await resetDeckStyle();
      setStyle(saved.style);
      setSummary(saved.style_summary);
      setMessage('Restored the defaults.');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Deck style</h1>
          <p class="dash-sub">{summary}</p>
        </div>
        <div class="dash-actions">
          <button type="button" class="primary" onClick={() => void save()}>
            Save
          </button>
          <button type="button" onClick={() => void reset()}>
            Reset
          </button>
          <a class="button" href="#/humans/reporting">
            Back
          </a>
        </div>
      </header>

      {message && <p class="dash-note">{message}</p>}
      {error && <NoticeBlock title="Could not save" items={[error]} />}

      <Card title="Slides">
        {options.style_fields.map((field: StyleField) => (
          <div key={field.key} class="field-row">
            <label for={`style-${field.key}`}>{field.label}</label>
            <StyleInput
              field={field}
              value={style[field.key]}
              choices={choicesFor(field.key)}
              colorRoles={options.style_choices.color_roles}
              label={label}
              onChange={(value) => set(field.key, value)}
            />
          </div>
        ))}
      </Card>
    </div>
  );
}

function StyleInput({
  field,
  value,
  choices,
  colorRoles,
  label,
  onChange,
}: {
  field: StyleField;
  value: string | number | boolean | undefined;
  choices: (string | number)[];
  colorRoles: string[];
  label: (key: string, value: string | number) => string;
  onChange: (value: string | number | boolean) => void;
}) {
  const id = `style-${field.key}`;
  if (field.kind === 'bool') {
    return <input id={id} type="checkbox" checked={Boolean(value)} onChange={() => onChange(!value)} />;
  }
  if (field.kind === 'color') {
    // A palette role or a literal hex — the backend resolves either, so the
    // input accepts both rather than forcing a colour picker on "accent".
    return (
      <span class="chip-row">
        <input
          id={id}
          type="text"
          value={String(value ?? '')}
          placeholder="accent or #2aaa69"
          onInput={(e) => onChange((e.target as HTMLInputElement).value)}
        />
        {colorRoles.map((role) => (
          <button key={role} type="button" onClick={() => onChange(role)}>
            {role}
          </button>
        ))}
      </span>
    );
  }
  if (field.kind === 'choice' || field.kind === 'int') {
    return (
      <select
        id={id}
        value={String(value ?? '')}
        onChange={(e) => {
          const raw = (e.target as HTMLSelectElement).value;
          onChange(field.kind === 'int' ? Number(raw) : raw);
        }}
      >
        {choices.map((choice) => (
          <option key={String(choice)} value={String(choice)}>
            {label(field.key, choice)}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      id={id}
      type="text"
      value={String(value ?? '')}
      onInput={(e) => onChange((e.target as HTMLInputElement).value)}
    />
  );
}
