// Saved plans — every planning session on this machine, resumable in a click.
// Served by the sessions_list tool (the same rows the TUI's resume list reads).

import { useEffect, useState } from 'react';
import { callTool } from '../api';

interface SessionRow {
  session_id: string;
  project_name?: string;
  last_node?: string;
  updated_at?: string;
  created_at?: string;
}

export function Sessions() {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    callTool<{ sessions: SessionRow[] }>('sessions_list').then(
      (envelope) => {
        if (!envelope.ok) {
          setError(envelope.error?.message ?? 'sessions_list failed');
          return;
        }
        setRows(envelope.data.sessions ?? []);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error) return <p>Could not load saved plans: {error}</p>;
  if (!rows) return <p>Loading…</p>;
  if (!rows.length)
    return (
      <div>
        <h1 class="page-title">Saved plans</h1>
        <p>Nothing yet. <a href="#/humans/planning">Start a plan</a> and it will show up here.</p>
      </div>
    );

  return (
    <div>
      <h1 class="page-title">Saved plans</h1>
      <ul class="session-list">
        {rows.map((row) => (
          <li key={row.session_id}>
            <a href={`#/humans/planning/chat?id=${encodeURIComponent(row.session_id)}`}>
              <strong>{row.project_name || 'Untitled plan'}</strong>
              <span class="session-meta">
                {row.last_node ? `${row.last_node} · ` : ''}
                {row.updated_at ?? row.created_at ?? ''}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
