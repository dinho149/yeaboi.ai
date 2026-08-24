// Team Analysis results — the cards a run earned.
//
// The card list and its order come from the backend (analysis/dashboard.py);
// every number below is read straight off the stored TeamProfile, so this page
// and the terminal's are looking at the same figures.

import { Card, Lozenge, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { useEffect, useState } from 'react';
import { maskText } from '../boards';
import { ResultActions } from '../components/ResultActions';
import { type AnalysisResult, loadAnalysisResult } from '../dashboards';

/** The result's team id rides the hash query so the route path stays a literal. */
export function teamIdFromHash(hash: string): string {
  const query = hash.indexOf('?');
  if (query < 0) return '';
  return new URLSearchParams(hash.slice(query + 1)).get('id') ?? '';
}

type Profile = Record<string, any>;

const pct = (value: number | undefined) => `${Math.round(value ?? 0)}%`;
const num = (value: number | undefined, digits = 1) => (value ?? 0).toFixed(digits);

export function AnalysisResults() {
  const [teamId, setTeamId] = useState(() => teamIdFromHash(window.location.hash));
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [open, setOpen] = useState('');
  const [error, setError] = useState('');
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');

  useEffect(() => {
    const onChange = () => setTeamId(teamIdFromHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  useEffect(() => {
    if (!teamId) {
      setError('No analysis was named — pick one from Team Analysis.');
      return;
    }
    loadAnalysisResult(teamId).then(
      (body) => {
        setResult(body);
        setOpen(body.cards[0]?.key ?? '');
      },
      (e: Error) => setError(e.message),
    );
  }, [teamId]);

  if (error) return <NoticeBlock title="Could not open that analysis" items={[error]} />;
  if (!result) return <p>Loading…</p>;

  const profile = result.profile as Profile;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">{maskText(profile.team_name || profile.project_key, mask)}</h1>
          <p class="dash-sub">
            {profile.source} · {profile.sample_sprints} sprints · {profile.sample_stories} stories ·{' '}
            {profile.updated_at || profile.created_at}
          </p>
        </div>
        <div class="dash-actions">
          <a href="#/humans/analysis">All analyses</a>
        </div>
      </header>

      <ResultActions
        refer={{ kind: 'analysis', session_id: teamId }}
        mode="analysis"
        anonNote={anonNote}
        onAnonymize={(replacements, note) => {
          setMask(replacements);
          setAnonNote(note);
        }}
      />

      <div class="card-rail">
        {result.cards.map((card) => (
          <button
            key={card.key}
            type="button"
            class={card.key === open ? 'rail-tab active' : 'rail-tab'}
            onClick={() => setOpen(card.key)}
          >
            {card.title}
          </button>
        ))}
      </div>

      <ResultCard
        cardKey={open}
        title={result.cards.find((c) => c.key === open)?.title ?? ''}
        profile={profile}
        examples={result.examples}
      />
    </div>
  );
}

function ResultCard({
  cardKey,
  title,
  profile,
  examples,
}: {
  cardKey: string;
  title: string;
  profile: Profile;
  examples: Record<string, any>;
}) {
  switch (cardKey) {
    case 'velocity':
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Velocity" value={`${num(profile.velocity_avg, 0)} pts`} />
            <StatTile label="Variance" value={`± ${num(profile.velocity_stddev, 0)}`} />
            <StatTile label="Completion" value={pct(profile.sprint_completion_rate)} />
            <StatTile label="Spill" value={pct(profile.spillover?.carried_over_pct)} />
          </StatGrid>
          <p>
            {profile.sprints_fully_completed} sprint{profile.sprints_fully_completed === 1 ? '' : 's'} finished whole,{' '}
            {profile.sprints_partially_completed} did not.
          </p>
          {profile.spillover?.most_common_spillover_reason && (
            <p class="dash-note">Most work spills because: {profile.spillover.most_common_spillover_reason}</p>
          )}
        </Card>
      );

    case 'team':
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Stories analysed" value={String(profile.sample_stories ?? 0)} />
            <StatTile label="Sprints analysed" value={String(profile.sample_sprints ?? 0)} />
            <StatTile
              label="Avg spill / sprint"
              value={`${num(profile.spillover?.avg_spillover_pts)} pts`}
            />
          </StatGrid>
          <ContributorList examples={examples} />
        </Card>
      );

    case 'estimation':
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Estimates hold" value={pct(profile.estimation_accuracy_pct)} />
          </StatGrid>
          <table class="data-table">
            <thead>
              <tr>
                <th>Points</th>
                <th>Cycle time</th>
                <th>Tasks</th>
                <th>Overshoot</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {(profile.point_calibrations ?? []).map((cal: Profile) => (
                <tr key={cal.point_value}>
                  <td>{cal.point_value}</td>
                  <td>{num(cal.avg_cycle_time_days)} days</td>
                  <td>{num(cal.typical_task_count)}</td>
                  <td>{pct(cal.overshoot_pct)}</td>
                  <td>{cal.sample_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(profile.point_calibrations ?? []).length && <p>Not enough estimated stories to calibrate points yet.</p>}
        </Card>
      );

    case 'workflow': {
      const dod = profile.dod_signal ?? {};
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Have comments" value={pct(dod.stories_with_comments_pct)} />
            <StatTile label="Link a PR" value={pct(dod.stories_with_pr_link_pct)} />
            <StatTile label="Mention review" value={pct(dod.stories_with_review_mention_pct)} />
            <StatTile label="Mention testing" value={pct(dod.stories_with_testing_mention_pct)} />
            <StatTile label="Mention deploy" value={pct(dod.stories_with_deploy_mention_pct)} />
          </StatGrid>
          <h3>What actually happens before a ticket closes</h3>
          <ul>
            {(dod.common_checklist_items ?? []).map((item: string) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          {!(dod.common_checklist_items ?? []).length && <p>No consistent pre-close behaviour was detectable.</p>}
        </Card>
      );
    }

    case 'writing': {
      const writing = profile.writing_patterns ?? {};
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Median ACs" value={num(writing.median_ac_count)} />
            <StatTile label="Median tasks" value={num(writing.median_task_count_per_story)} />
            <StatTile label="Stories with subtasks" value={pct(writing.stories_with_subtasks_pct)} />
            <StatTile label="Epics described" value={pct(writing.epics_with_description_pct)} />
          </StatGrid>
          <p>
            <Lozenge category={writing.uses_given_when_then ? 'done' : 'todo'} small>
              {writing.uses_given_when_then ? 'Given/When/Then' : 'Prose acceptance criteria'}
            </Lozenge>{' '}
            <Lozenge category={writing.subtasks_use_consistent_naming ? 'done' : 'todo'} small>
              {writing.subtasks_use_consistent_naming ? 'Consistent subtask naming' : 'Subtask naming varies'}
            </Lozenge>
          </p>
          {(writing.common_personas ?? []).length ? (
            <p class="dash-note">Personas used: {(writing.common_personas ?? []).join(', ')}</p>
          ) : null}
          {(writing.common_subtask_patterns ?? []).length ? (
            <>
              <h3>Recurring subtasks</h3>
              <ul>
                {(writing.common_subtask_patterns ?? []).map((pattern: string) => (
                  <li key={pattern}>{pattern}</li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>
      );
    }

    case 'trends':
      return (
        <Card title={title}>
          <h3>Story shapes by discipline</h3>
          <table class="data-table">
            <thead>
              <tr>
                <th>Discipline</th>
                <th>Avg points</th>
                <th>Avg ACs</th>
                <th>Avg tasks</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {(profile.story_shapes ?? []).map((shape: Profile) => (
                <tr key={shape.discipline}>
                  <td>{shape.discipline}</td>
                  <td>{num(shape.avg_points)}</td>
                  <td>{num(shape.avg_ac_count)}</td>
                  <td>{num(shape.avg_task_count)}</td>
                  <td>{shape.sample_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(profile.story_shapes ?? []).length && <p>Not enough labelled stories to compare disciplines.</p>}
        </Card>
      );

    case 'recommendations':
      return (
        <Card title={title}>
          <RecommendationList examples={examples} />
        </Card>
      );

    case 'ai-adoption': {
      const ai = profile.ai_adoption ?? {};
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Footprint" value={pct(ai.footprint_pct)} />
            <StatTile label="Commits read" value={String(ai.scanned_commits ?? 0)} />
            <StatTile label="PRs read" value={String(ai.scanned_prs ?? 0)} />
            <StatTile label="AI-marked" value={String((ai.ai_commits ?? 0) + (ai.ai_prs ?? 0))} />
          </StatGrid>
          <NoticeBlock
            title="This is a lower bound"
            items={[
              'Only tools that leave a textual trace in commit or PR metadata are counted. Inline autocomplete leaves none, so real usage is at least this.',
            ]}
          />
          {(ai.per_tool ?? []).length ? (
            <>
              <h3>By tool</h3>
              <ul>
                {(ai.per_tool ?? []).map(([tool, count]: [string, number]) => (
                  <li key={tool}>
                    {tool} — {count}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {(ai.repos_scanned ?? []).length ? (
            <p class="dash-note">Scanned: {(ai.repos_scanned ?? []).join(', ')}</p>
          ) : null}
        </Card>
      );
    }

    case 'code-health':
      return (
        <Card title={title}>
          <HealthTable examples={examples} />
        </Card>
      );

    case 'documentation': {
      const docs = profile.doc_quality ?? {};
      return (
        <Card title={title}>
          <StatGrid>
            <StatTile label="Pages read" value={String(docs.pages_scanned ?? 0)} />
            <StatTile label="Clarity" value={num(docs.avg_clarity, 0)} />
            <StatTile label="Usefulness" value={num(docs.avg_usefulness, 0)} />
            <StatTile label="With an owner" value={String(docs.owned_pages ?? 0)} />
          </StatGrid>
          <p>
            {docs.clear_pages ?? 0} clear · {docs.mixed_pages ?? 0} mixed · {docs.unclear_pages ?? 0} unclear ·{' '}
            {docs.ai_marked_pages ?? 0} disclose AI help
          </p>
          {(docs.flagged_pages ?? []).length ? (
            <>
              <h3>Worth a look</h3>
              <ul>
                {(docs.flagged_pages ?? []).map(([pageTitle, reason]: [string, string]) => (
                  <li key={pageTitle}>
                    <strong>{pageTitle}</strong> — {reason}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>
      );
    }

    case 'insights':
      return (
        <Card title={title}>
          <InsightGroups examples={examples} />
        </Card>
      );

    default:
      return null;
  }
}

function ContributorList({ examples }: { examples: Record<string, any> }) {
  const contributors: Profile[] = examples?.contributors ?? examples?.member_activity ?? [];
  if (!contributors.length) return <p>No per-person breakdown in this run.</p>;
  return (
    <ul class="member-list">
      {contributors.map((person) => (
        <li key={person.name}>
          <strong>{person.name}</strong>
          <span>
            {person.stories ?? person.completed ?? 0} stories · {num(person.points ?? 0, 0)} pts
          </span>
        </li>
      ))}
    </ul>
  );
}

function RecommendationList({ examples }: { examples: Record<string, any> }) {
  const items: (string | Profile)[] = examples?.recommendations ?? [];
  if (!items.length) return <p>Nothing to recommend — this team's numbers hold together.</p>;
  return (
    <ul>
      {items.map((item, index) => (
        <li key={typeof item === 'string' ? item : index}>{typeof item === 'string' ? item : item.text}</li>
      ))}
    </ul>
  );
}

function HealthTable({ examples }: { examples: Record<string, any> }) {
  const health = examples?.repository_health ?? examples?.ai_adoption?.repository_health ?? {};
  const rows = Object.entries(health as Record<string, any>);
  if (!rows.length) return <p>No code-health signal in this run.</p>;
  return (
    <table class="data-table">
      <thead>
        <tr>
          <th>Repository</th>
          <th>Signal</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([repo, value]) => (
          <tr key={repo}>
            <td>{repo}</td>
            <td>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InsightGroups({ examples }: { examples: Record<string, any> }) {
  const insights = examples?.insights ?? {};
  const groups: [string, string][] = [
    ['start', 'Start'],
    ['stop', 'Stop'],
    ['keep', 'Keep'],
    ['try', 'Try'],
  ];
  const any = groups.some(([key]) => (insights[key] ?? []).length);
  if (!any) return <p>No coaching insights in this run — it may have been a quick pass.</p>;
  return (
    <div class="insight-groups">
      {groups.map(([key, label]) => (
        <div key={key}>
          <h3>{label}</h3>
          <ul>
            {(insights[key] ?? []).map((item: string | Profile, index: number) => (
              <li key={typeof item === 'string' ? item : index}>{typeof item === 'string' ? item : item.text}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
