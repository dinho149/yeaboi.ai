// What's New — the bundled changelog (the same 113-release ledger the TUI's
// `c` keycap shows), with the per-area chips colour-matched to the modes.

import { useEffect, useState } from 'react';
import { apiGet } from '../api';

interface Highlight {
  text: string;
  areas: string[];
}

interface Entry {
  version: string;
  date: string;
  summary: string;
  highlights: Highlight[];
}

const PAGE_SIZE = 20;

export function WhatsNew() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [shown, setShown] = useState(PAGE_SIZE);
  const [area, setArea] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ entries: Entry[] }>('/api/meta/changelog').then(
      ({ entries: loaded }) => setEntries(loaded),
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error) return <p>Could not load the changelog: {error}</p>;
  if (!entries) return <p>Loading…</p>;

  const areas = [...new Set(entries.flatMap((e) => e.highlights.flatMap((h) => h.areas)))].sort();
  const visible = (
    area ? entries.filter((e) => e.highlights.some((h) => h.areas.includes(area))) : entries
  ).slice(0, shown);

  return (
    <div>
      <h1 class="page-title">What's New</h1>
      <p class="area-filter">
        <button class={area === null ? 'active' : ''} onClick={() => setArea(null)}>
          all
        </button>
        {areas.map((name) => (
          <button key={name} class={area === name ? 'active' : ''} onClick={() => setArea(name)}>
            {name}
          </button>
        ))}
      </p>
      {visible.map((entry) => (
        <section key={entry.version} class="release">
          <h3>
            v{entry.version} <span class="date">{entry.date}</span>
          </h3>
          {entry.summary && <p class="summary">{entry.summary}</p>}
          <ul>
            {(area ? entry.highlights.filter((h) => h.areas.includes(area)) : entry.highlights).map((h) => (
              <li key={h.text}>{h.text}</li>
            ))}
          </ul>
        </section>
      ))}
      {visible.length < entries.length && !area && (
        <p>
          <button onClick={() => setShown((n) => n + PAGE_SIZE)}>Show older releases</button>
        </p>
      )}
    </div>
  );
}
