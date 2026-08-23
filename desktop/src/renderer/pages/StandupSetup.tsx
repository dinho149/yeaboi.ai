// Standup setup — who is on the team, which repos count, which docs count,
// and how your own activity is attributed. The terminal's four configure
// flows, as one page of sections over standup_config_get/_set.
//
// Discovery is explicit: a team or repository list is only fetched when asked
// for, because each is a paged API sweep.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { callTool } from '../api';
import { loadStandup } from '../dashboards';

interface Config {
  tracker_sources: string[];
  team_members: string[];
  code_sources: string[];
  github_owners: string[];
  github_repositories: string[];
  github_excluded_repositories: string[];
  azdo_projects: string[];
  documentation_sources: string[];
  repo_path: string;
  my_aliases: string;
  transcript_dir: string;
  transcript_review_enabled: boolean;
}

const TRACKERS = ['jira', 'azure_devops'];
const CODE_SOURCES = ['github', 'azure_devops'];
const DOC_SOURCES = ['confluence', 'notion'];

const EMPTY: Config = {
  tracker_sources: [],
  team_members: [],
  code_sources: [],
  github_owners: [],
  github_repositories: [],
  github_excluded_repositories: [],
  azdo_projects: [],
  documentation_sources: [],
  repo_path: '',
  my_aliases: '',
  transcript_dir: '',
  transcript_review_enabled: true,
};

export function StandupSetup() {
  const [sessionId, setSessionId] = useState('');
  const [config, setConfig] = useState<Config | null>(null);
  const [candidates, setCandidates] = useState<string[] | null>(null);
  const [owners, setOwners] = useState<{ github_owners: string[]; azdo_projects: string[] } | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');

  useEffect(() => {
    loadStandup().then(
      async (dash) => {
        setSessionId(dash.session_id);
        const envelope = await callTool<{ config: Config | null }>('standup_config_get', {
          session_id: dash.session_id,
        });
        setConfig({ ...EMPTY, ...(envelope.data?.config ?? {}) });
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  async function discoverTeam() {
    setBusy('team');
    const envelope = await callTool<{ members: { name?: string }[] | string[] }>('standup_members', {
      session_id: sessionId,
      tracker_sources: config?.tracker_sources.length ? config.tracker_sources : null,
    });
    setBusy('');
    if (!envelope.ok) return setError(envelope.error?.message ?? 'standup_members failed');
    setCandidates(
      (envelope.data.members as (string | { name?: string })[]).map((m) => (typeof m === 'string' ? m : (m.name ?? ''))),
    );
  }

  async function discoverRepos() {
    setBusy('repos');
    const envelope = await callTool<{ github_owners: string[]; azdo_projects: string[] }>('standup_repositories', {
      code_sources: config?.code_sources.length ? config.code_sources : null,
    });
    setBusy('');
    if (!envelope.ok) return setError(envelope.error?.message ?? 'standup_repositories failed');
    setOwners({ github_owners: envelope.data.github_owners, azdo_projects: envelope.data.azdo_projects });
  }

  async function save() {
    if (!config) return;
    setBusy('save');
    setError('');
    setNote('');
    const envelope = await callTool('standup_config_set', { session_id: sessionId, ...config });
    setBusy('');
    if (envelope.ok) setNote('Saved.');
    else setError(envelope.error?.message ?? 'standup_config_set failed');
  }

  if (error && !config) return <NoticeBlock title="Could not load the setup" items={[error]} />;
  if (!config) return <p>Loading…</p>;

  const set = (patch: Partial<Config>) => setConfig({ ...config, ...patch });
  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <div class="dash">
      <h1 class="page-title">Standup setup</h1>
      <p class="dash-sub">What the standup reads, and who it reads it for.</p>

      <Card title="Team" actions={<button type="button" disabled={!!busy} onClick={() => void discoverTeam()}>
        {busy === 'team' ? 'Looking…' : 'Find people'}
      </button>}>
        <div class="chip-row">
          {TRACKERS.map((source) => (
            <label key={source} class="check-row">
              <input
                type="checkbox"
                checked={config.tracker_sources.includes(source)}
                onChange={() => set({ tracker_sources: toggle(config.tracker_sources, source) })}
              />
              <span>{source}</span>
            </label>
          ))}
        </div>
        {candidates ? (
          <div class="chip-row">
            {candidates.map((name) => (
              <label key={name} class="check-row">
                <input
                  type="checkbox"
                  checked={config.team_members.includes(name)}
                  onChange={() => set({ team_members: toggle(config.team_members, name) })}
                />
                <span>{name}</span>
              </label>
            ))}
            {!candidates.length && <p>No candidates came back — check the tracker credentials in Settings.</p>}
          </div>
        ) : (
          <p class="dash-note">
            {config.team_members.length
              ? `${config.team_members.length} selected: ${config.team_members.join(', ')}`
              : 'Nobody selected yet — the standup runs self-only.'}
          </p>
        )}
      </Card>

      <Card title="Code" actions={<button type="button" disabled={!!busy} onClick={() => void discoverRepos()}>
        {busy === 'repos' ? 'Looking…' : 'Find repositories'}
      </button>}>
        <div class="chip-row">
          {CODE_SOURCES.map((source) => (
            <label key={source} class="check-row">
              <input
                type="checkbox"
                checked={config.code_sources.includes(source)}
                onChange={() => set({ code_sources: toggle(config.code_sources, source) })}
              />
              <span>{source}</span>
            </label>
          ))}
        </div>
        {owners ? (
          <>
            <h3>GitHub owners</h3>
            <p class="dash-note">One owner covers every active repository inside it.</p>
            <div class="chip-row">
              {owners.github_owners.map((owner) => (
                <label key={owner} class="check-row">
                  <input
                    type="checkbox"
                    checked={config.github_owners.includes(owner)}
                    onChange={() => set({ github_owners: toggle(config.github_owners, owner) })}
                  />
                  <span>{owner}</span>
                </label>
              ))}
            </div>
            <h3>Azure DevOps projects</h3>
            <div class="chip-row">
              {owners.azdo_projects.map((project) => (
                <label key={project} class="check-row">
                  <input
                    type="checkbox"
                    checked={config.azdo_projects.includes(project)}
                    onChange={() => set({ azdo_projects: toggle(config.azdo_projects, project) })}
                  />
                  <span>{project}</span>
                </label>
              ))}
            </div>
          </>
        ) : (
          <p class="dash-note">
            {config.github_owners.length || config.azdo_projects.length
              ? [...config.github_owners, ...config.azdo_projects].join(', ')
              : 'No code scope yet.'}
          </p>
        )}
      </Card>

      <Card title="Documentation">
        <div class="chip-row">
          {DOC_SOURCES.map((source) => (
            <label key={source} class="check-row">
              <input
                type="checkbox"
                checked={config.documentation_sources.includes(source)}
                onChange={() => set({ documentation_sources: toggle(config.documentation_sources, source) })}
              />
              <span>{source}</span>
            </label>
          ))}
        </div>
        <p class="dash-note">Repository documentation follows the code repositories selected above.</p>
      </Card>

      <Card title="You">
        <div class="field-row">
          <label for="standup-repo">Local repository</label>
          <input
            id="standup-repo"
            type="text"
            value={config.repo_path}
            placeholder="/Users/you/code/project"
            onInput={(e) => set({ repo_path: (e.target as HTMLInputElement).value })}
          />
        </div>
        <div class="field-row">
          <label for="standup-aliases">Your other names</label>
          <input
            id="standup-aliases"
            type="text"
            value={config.my_aliases}
            placeholder="ana, ana.dev, a.smith@work.com"
            onInput={(e) => set({ my_aliases: (e.target as HTMLInputElement).value })}
          />
        </div>
        <p class="dash-note">Commit authors and tracker names that are also you, so your activity lands on your card.</p>
        <div class="field-row">
          <label for="standup-transcripts">Transcript folder</label>
          <input
            id="standup-transcripts"
            type="text"
            value={config.transcript_dir}
            placeholder="~/.yeaboi/transcripts"
            onInput={(e) => set({ transcript_dir: (e.target as HTMLInputElement).value })}
          />
        </div>
        <label class="check-row">
          <input
            type="checkbox"
            checked={config.transcript_review_enabled}
            onChange={() => set({ transcript_review_enabled: !config.transcript_review_enabled })}
          />
          <span>Review yesterday's meeting before today's standup</span>
        </label>
      </Card>

      {error && <NoticeBlock title="Could not save" items={[error]} />}
      {note && <p class="dash-note">{note}</p>}

      <div class="dash-actions">
        <button type="button" class="primary" disabled={!!busy} onClick={() => void save()}>
          {busy === 'save' ? 'Saving…' : 'Save setup'}
        </button>
        <a href="#/humans/standup">Back to the standup</a>
      </div>
    </div>
  );
}
