// Performance — the roster, and where each engineer stands.

import { Card, NoticeBlock } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type PerformanceRoster, loadPerformanceRoster } from '../modes';

export function Performance() {
  const [roster, setRoster] = useState<PerformanceRoster | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    loadPerformanceRoster().then(setRoster, (e: Error) => setError(e.message));
  }, []);

  if (error) return <NoticeBlock title="Could not load the roster" items={[error]} />;
  if (!roster) return <p>Loading…</p>;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Performance</h1>
          <p class="dash-sub">
            1:1 prep, completion and the 6-month review — for the people who did the work on the board.
          </p>
        </div>
      </header>

      {roster.engineers.length > 0 ? (
        <div class="profile-list">
          {roster.engineers.map((engineer) => (
            <a
              key={engineer.name}
              class="profile-row"
              href={`#/humans/performance/engineer?name=${encodeURIComponent(engineer.name)}`}
            >
              <Card title={engineer.name}>
                <p class="dash-note">{engineer.hint}</p>
              </Card>
            </a>
          ))}
        </div>
      ) : (
        <Card title="No engineers yet">
          <p>
            <Duck state="idle" size={28} /> {roster.empty_message}
          </p>
        </Card>
      )}
    </div>
  );
}
