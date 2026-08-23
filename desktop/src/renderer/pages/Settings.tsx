// Settings — the three tabs (Credentials / Sharing / System) over the
// /api/settings snapshot. Field behaviour mirrors the TUI settings page:
// choice rows cycle a fixed set, secrets are write-only (masked preview,
// replace to change), and the special rows (subscription sign-in, data
// directory, allowed paths, voice device) get their dedicated flows.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useMemo, useState } from 'react';
import { SignInPanel } from '../components/SignInPanel';
import {
  type ProviderCatalog,
  type SettingField,
  type SettingsSnapshot,
  loadProviders,
  loadSettings,
  saveAllowedPaths,
  saveDataDir,
  saveSetting,
} from '../settings';

import { SETTINGS_TABS } from '../settings-tabs';

const SECTION_TITLES: Record<string, string> = {
  provider: 'LLM Provider',
  jira: 'Jira',
  azure: 'Azure DevOps',
  github: 'GitHub',
  notion: 'Notion',
  slack: 'Slack',
  sharing: 'Sharing',
  storage: 'Storage',
  standup: 'Daily Standup',
  voice: 'Voice Input',
  advanced: 'Advanced',
};

function activeChoice(fields: SettingField[], env: string): string {
  return fields.find((f) => f.env === env)?.active_choice ?? '';
}

/** The provider section shows only the rows the live provider actually uses —
    same rule as the TUI: a key belonging to a provider you are not on stays on
    disk untouched, and its row comes back when you switch back. */
function visibleProviderEnvs(fields: SettingField[], catalog: ProviderCatalog | null): Set<string> {
  const visible = new Set(['LLM_PROVIDER', 'LLM_MODEL']);
  const provider = activeChoice(fields, 'LLM_PROVIDER');
  if (provider === 'anthropic') {
    visible.add('ANTHROPIC_AUTH_MODE');
    visible.add(activeChoice(fields, 'ANTHROPIC_AUTH_MODE') === 'subscription' ? 'CLAUDE_CODE_OAUTH_TOKEN' : 'ANTHROPIC_API_KEY');
  } else if (provider === 'bedrock') {
    visible.add('AWS_REGION');
    visible.add('AWS_PROFILE');
  } else if (provider === 'ollama') {
    visible.add('OLLAMA_BASE_URL');
    visible.add('OLLAMA_NUM_CTX');
  } else {
    const card = catalog?.providers.find((p) => p.provider_val === provider);
    if (card?.env_var) visible.add(card.env_var);
  }
  return visible;
}

export function Settings() {
  const path = window.location.hash.slice(1);
  // The tab table is a non-empty literal; index 0 only looks optional to
  // noUncheckedIndexedAccess.
  const tab = SETTINGS_TABS.find((t) => t.route === path) ?? SETTINGS_TABS[0]!;

  const [snapshot, setSnapshot] = useState<SettingsSnapshot | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [signingIn, setSigningIn] = useState(false);
  const [moveAsk, setMoveAsk] = useState<string | null>(null);

  const refresh = () => loadSettings().then(setSnapshot, (e: Error) => setError(e.message));
  useEffect(() => {
    void refresh();
    loadProviders().then(setCatalog, () => undefined);
  }, []);

  const save = async (key: string, value: string) => {
    try {
      const result = await saveSetting(key, value);
      setStatus(result.message);
      if (result.restart_required) setRestartNeeded(true);
      await refresh();
    } catch (e) {
      setStatus((e as Error).message);
    }
    setEditing(null);
  };

  const providerVisible = useMemo(
    () => (snapshot ? visibleProviderEnvs(snapshot.fields, catalog) : new Set<string>()),
    [snapshot, catalog],
  );

  if (error) return <p>Could not load settings: {error}</p>;
  if (!snapshot) return <p>Loading…</p>;

  const shareAccess = activeChoice(snapshot.fields, 'YEABOI_SHARE_MODE') === 'access';
  const knownSections = new Set(SETTINGS_TABS.flatMap((t) => t.sections));

  const sectionFields = (section: string): SettingField[] =>
    snapshot.fields.filter((f) => {
      if (f.section !== section) return false;
      if (section === 'provider') return providerVisible.has(f.env);
      if (section === 'sharing' && f.env.startsWith('CLOUDFLARE_')) return shareAccess;
      return true;
    });

  // Sections the backend grows later land on the System tab rather than nowhere.
  const sections =
    tab.title === 'System'
      ? [...tab.sections, ...snapshot.sections.filter((s) => !knownSections.has(s))]
      : [...tab.sections];

  const beginEdit = (field: SettingField) => {
    setEditing(field.env);
    // Secrets are write-only: the editor always starts empty rather than with
    // the masked preview, so a save can never write dots back over a key.
    setDraft(field.secret ? '' : field.value);
  };

  const renderRow = (field: SettingField) => {
    if (field.choices.length > 0) {
      return (
        <div class="settings-row" key={field.env}>
          <span class="settings-label">{field.label}</span>
          <span class="settings-choices">
            {field.choices.map((opt) => (
              <button
                key={opt}
                class={opt === field.active_choice ? 'choice active' : 'choice'}
                onClick={() => void save(field.env, opt)}
              >
                {field.choice_labels[opt] ?? opt}
              </button>
            ))}
          </span>
        </div>
      );
    }

    if (field.action === 'signin') {
      return (
        <div class="settings-row" key={field.env}>
          <span class="settings-label">{field.label}</span>
          <span class="settings-value">{field.is_set ? field.value : 'not signed in'}</span>
          <button onClick={() => setSigningIn(true)}>Sign in…</button>
        </div>
      );
    }

    if (field.action === 'allowed-paths') {
      return <AllowedPathsRow key={field.env} field={field} onSaved={(m) => (setStatus(m), void refresh())} />;
    }

    if (field.action === 'voice-device') {
      const devices = snapshot.voice.devices;
      return (
        <div class="settings-row" key={field.env}>
          <span class="settings-label">{field.label}</span>
          <select
            value={field.value}
            onChange={(event) => void save(field.env, (event.target as HTMLSelectElement).value)}
          >
            <option value="">system default</option>
            {devices.map((d) => (
              <option key={d.index} value={d.name}>
                {d.name}
                {d.is_default ? ' (default)' : ''}
              </option>
            ))}
            {field.value && !devices.some((d) => d.name === field.value) && (
              <option value={field.value}>{field.value} (saved)</option>
            )}
          </select>
        </div>
      );
    }

    const isEditing = editing === field.env;
    return (
      <div class="settings-row" key={field.env}>
        <span class="settings-label">{field.label}</span>
        {isEditing ? (
          <form
            class="settings-edit"
            onSubmit={(event) => {
              event.preventDefault();
              if (field.action === 'data-dir') {
                setEditing(null);
                setMoveAsk(draft.trim());
              } else {
                void save(field.env, draft);
              }
            }}
          >
            <input
              autofocus
              type={field.secret ? 'password' : 'text'}
              value={draft}
              placeholder={field.secret ? 'paste the new value' : ''}
              onInput={(event) => setDraft((event.target as HTMLInputElement).value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setEditing(null);
              }}
            />
            <button type="submit">Save</button>
            <button type="button" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </form>
        ) : (
          <>
            <span class={field.is_set ? 'settings-value' : 'settings-value unset'}>
              {field.is_set ? field.value : field.default ? `${field.default} (default)` : 'not set'}
            </span>
            <button onClick={() => beginEdit(field)}>Edit</button>
          </>
        )}
        {field.help_url && (
          <div class="settings-help">
            create:{' '}
            <a href={field.help_url} target="_blank" rel="noreferrer">
              {field.help_url}
            </a>
            <div class="settings-scope">scope: {field.help_scope}</div>
          </div>
        )}
      </div>
    );
  };

  const extraRows = (section: string) => {
    if (section === 'slack') {
      const twoWay =
        Boolean(snapshot.fields.find((f) => f.env === 'SLACK_BOT_TOKEN')?.is_set) &&
        Boolean(snapshot.fields.find((f) => f.env === 'SLACK_CHANNEL_ID')?.is_set);
      return (
        <div class="settings-row readonly">
          <span class="settings-label">Reads back</span>
          <span class={twoWay ? 'settings-value good' : 'settings-value unset'}>
            {twoWay ? 'yes — reactions and thread replies' : 'no — a webhook cannot be answered'}
          </span>
        </div>
      );
    }
    if (section === 'voice') {
      return (
        <div class="settings-row readonly">
          <span class="settings-label">Dictation</span>
          <span class="settings-value">{snapshot.voice.detail || snapshot.voice.state}</span>
        </div>
      );
    }
    if (section === 'advanced') {
      return (
        <div class="settings-row readonly">
          <span class="settings-label">Config File</span>
          <span class="settings-value">{snapshot.config_path}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <div>
      <h1 class="page-title">Settings</h1>
      <nav class="settings-tabs">
        {SETTINGS_TABS.map((t) => (
          <a key={t.route} href={`#${t.route}`} aria-current={t.route === tab.route ? 'page' : undefined}>
            {t.title}
          </a>
        ))}
      </nav>

      {restartNeeded && (
        <div class="settings-banner">Restart yeaboi (quit and reopen the app) to fully apply the data directory.</div>
      )}
      {status && (
        <div class="settings-status">
          <Duck state="idle" size={20} /> {status}
        </div>
      )}

      {signingIn && (
        <SignInPanel
          onClose={(saved, message) => {
            setSigningIn(false);
            setStatus(message);
            if (saved) void refresh();
          }}
        />
      )}

      {moveAsk !== null && (
        <div class="settings-dialog">
          <p>
            Move the existing data (sessions, exports, logs) to{' '}
            <code>{moveAsk || '~/.yeaboi (the default location)'}</code>?
          </p>
          <div class="settings-dialog-actions">
            <button
              onClick={() => {
                const value = moveAsk;
                setMoveAsk(null);
                void saveDataDir(value, true).then(
                  (r) => (setStatus(r.message), setRestartNeeded(true), void refresh()),
                  (e: Error) => setStatus(e.message),
                );
              }}
            >
              Move data
            </button>
            <button
              onClick={() => {
                const value = moveAsk;
                setMoveAsk(null);
                void saveDataDir(value, false).then(
                  (r) => (setStatus(r.message), setRestartNeeded(true), void refresh()),
                  (e: Error) => setStatus(e.message),
                );
              }}
            >
              Just set the path
            </button>
            <button onClick={() => setMoveAsk(null)}>Cancel</button>
          </div>
        </div>
      )}

      {sections.map((section) => {
        const fields = sectionFields(section);
        const extras = extraRows(section);
        if (fields.length === 0 && !extras) return null;
        return (
          <section class="settings-box" key={section}>
            <h2>{SECTION_TITLES[section] ?? section}</h2>
            {fields.map(renderRow)}
            {extras}
          </section>
        );
      })}
    </div>
  );
}

function AllowedPathsRow({ field, onSaved }: { field: SettingField; onSaved: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [paths, setPaths] = useState<string[]>([]);
  const [next, setNext] = useState('');

  const begin = () => {
    setPaths(field.value ? field.value.split(',').filter(Boolean) : []);
    setOpen(true);
  };

  if (!open) {
    return (
      <div class="settings-row">
        <span class="settings-label">{field.label}</span>
        <span class={field.is_set ? 'settings-value' : 'settings-value unset'}>
          {field.value || 'none — sandboxed to data dir'}
        </span>
        <button onClick={begin}>Edit</button>
      </div>
    );
  }

  return (
    <div class="settings-row settings-paths">
      <span class="settings-label">{field.label}</span>
      <div class="settings-paths-editor">
        {paths.map((p) => (
          <div key={p} class="settings-path">
            <code>{p}</code>
            <button onClick={() => setPaths(paths.filter((x) => x !== p))}>✕</button>
          </div>
        ))}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const value = next.trim();
            if (value && !paths.includes(value)) setPaths([...paths, value]);
            setNext('');
          }}
        >
          <input
            value={next}
            placeholder="/path/to/allow"
            onInput={(event) => setNext((event.target as HTMLInputElement).value)}
          />
          <button type="submit">Add</button>
        </form>
        <div class="settings-dialog-actions">
          <button
            onClick={() => {
              void saveAllowedPaths(paths).then(
                (r) => (setOpen(false), onSaved(r.message)),
                (e: Error) => onSaved(e.message),
              );
            }}
          >
            Save
          </button>
          <button onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
