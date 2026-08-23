// Usage — lifetime LLM spend, served by the usage_get tool (the same numbers
// the TUI's Usage page reads from the token_usage table).

import { StatGrid, StatTile } from '@design/primitives/Stat';
import { useEffect, useState } from 'react';
import { callTool } from '../api';

interface UsageData {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  call_count: number;
  note?: string;
}

export function Usage() {
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    callTool<UsageData>('usage_get').then((envelope) => {
      if (!envelope.ok) {
        setError(envelope.error?.message ?? 'usage_get failed');
        return;
      }
      setUsage(envelope.data);
      setWarnings(envelope.warnings);
    }, (e: Error) => setError(e.message));
  }, []);

  if (error) return <p>Could not load usage: {error}</p>;
  if (!usage) return <p>Loading…</p>;

  const fmt = (n: number) => n.toLocaleString('en-US');

  return (
    <div>
      <h1 class="page-title">Usage</h1>
      <StatGrid>
        <StatTile label="LLM calls" value={fmt(usage.call_count)} />
        <StatTile label="Input tokens" value={fmt(usage.input_tokens)} />
        <StatTile label="Output tokens" value={fmt(usage.output_tokens)} />
        <StatTile label="Total tokens" value={fmt(usage.total_tokens)} />
      </StatGrid>
      {usage.note && <p style={{ color: 'var(--muted)' }}>{usage.note}</p>}
      {warnings.map((w) => (
        <p key={w} style={{ color: 'var(--muted)' }}>
          {w}
        </p>
      ))}
    </div>
  );
}
