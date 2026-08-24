// Team Analysis — the saved-runs hub. Every analysis this machine has done,
// newest first, plus the way into a new one.

import { Card, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type ProfileSummary, loadProfiles } from '../dashboards';

export function Analysis() {
  const [profiles, setProfiles] = useState<ProfileSummary[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    loadProfiles().then(
      (body) => setProfiles(body.profiles),
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error) return <NoticeBlock title="Could not load saved analyses" items={[error]} />;
  if (!profiles) return <p>Loading…</p>;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Team Analysis</h1>
          <p class="dash-sub">How this team actually delivers — read off the tracker, the code and the docs.</p>
        </div>
        <div class="dash-actions">
          <a class="button primary" href="#/humans/analysis/new">
            New analysis
          </a>
        </div>
      </header>

      {profiles.length ? (
        <div class="profile-list">
          {profiles.map((profile) => (
            <a key={profile.team_id} class="profile-row" href={`#/humans/analysis/results?id=${encodeURIComponent(profile.team_id)}`}>
              <Card interactive title={profile.team_name || profile.project_key}>
                <StatGrid>
                  <StatTile label="Source" value={profile.source} />
                  <StatTile label="Sprints" value={String(profile.sample_sprints)} />
                  <StatTile label="Stories" value={String(profile.sample_stories)} />
                  <StatTile label="Velocity" value={profile.velocity_avg.toFixed(0)} />
                </StatGrid>
                <p class="dash-note">{profile.analyzed_at}</p>
              </Card>
            </a>
          ))}
        </div>
      ) : (
        <Card title="Nothing analysed yet">
          <p>
            <Duck state="idle" size={28} /> Point yeaboi at your tracker and it will read the last few sprints — how
            fast the team goes, what a point means here, and where work spills.
          </p>
          <p>
            <a class="button primary" href="#/humans/analysis/new">
              Run the first analysis
            </a>
          </p>
        </Card>
      )}
    </div>
  );
}
