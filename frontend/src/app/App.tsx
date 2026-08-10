/**
 * The app shell: masthead, rail, and whatever the route resolves to.
 *
 * TODO(design): the composition here is structural. Which regions exist and what
 * fills them is the point; how they look is the design pass. The rail lists the
 * modes because the mode accents in `design/palette.css` already *are* this
 * product's information architecture - reusing them is why nav needs no new
 * colour decision.
 */

import { useEffect, useMemo, useState } from 'react';
import { Credit } from '../shared/Credit';
import { del, get, post } from './api';
import { interceptLinks, navigate, Routes } from './router';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';
import type { ProjectDetail, ProjectSummary, User } from './types';
import styles from './app.module.css';

const ROUTES = new Routes(['/', '/projects', '/projects/{id}', '/settings']);

/** Modes, in the order the TUI lists them. */
const RAIL = [
  { href: '/projects', label: 'Projects' },
  { href: '/settings', label: 'Settings' },
];

function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const sync = () => setPath(window.location.pathname);
    window.addEventListener('popstate', sync);
    const release = interceptLinks(sync);
    return () => {
      window.removeEventListener('popstate', sync);
      release();
    };
  }, []);
  return path;
}

function SignIn({ onDone }: { onDone: (user: User) => void }) {
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: Event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const result = await post<{ user: User }>('/api/auth/session', { email });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onDone(result.data.user);
  }

  return (
    <form className={styles.signin} onSubmit={submit}>
      <div className={styles.field}>
        <label className={styles.label} htmlFor="email">
          Email
        </label>
        <input
          id="email"
          className={styles.input}
          type="email"
          required
          value={email}
          onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
        />
      </div>
      {error ? <p className={styles.error}>{error}</p> : null}
      <button type="submit" className={styles.action} disabled={busy}>
        {busy ? 'Signing in' : 'Continue'}
      </button>
    </form>
  );
}

function ProjectList() {
  const state = useAsync(
    () => get<{ projects: ProjectSummary[] }>('/api/projects'),
    [],
    { isEmpty: (data) => data.projects.length === 0 },
  );

  async function create() {
    const name = window.prompt('Project name');
    if (!name) return;
    await post('/api/projects', { name });
    navigate('/projects');
  }

  return (
    <AsyncView
      state={state}
      empty={
        <EmptyState
          title="No projects yet"
          hint="A project holds the plans, standups and retros for one team."
          action={
            <button type="button" className={styles.action} onClick={create}>
              New project
            </button>
          }
        />
      }
    >
      {(data) => (
        <ul className={styles.projectList}>
          {data.projects.map((project) => (
            <li key={project.id} className={styles.projectRow}>
              <a className={styles.projectName} href={`/projects/${project.id}`}>
                {project.name}
              </a>
              <span className={styles.role}>{project.role}</span>
            </li>
          ))}
        </ul>
      )}
    </AsyncView>
  );
}

function ProjectDetailView({ id }: { id: string }) {
  const state = useAsync(() => get<ProjectDetail>(`/api/projects/${id}`), [id]);
  return (
    <AsyncView state={state} empty={<EmptyState title="Nothing here" />}>
      {(project) => (
        <section>
          <h1>{project.name}</h1>
          <ul className={styles.projectList}>
            {project.members.map((member) => (
              <li key={member.id} className={styles.projectRow}>
                <span className={styles.projectName}>{member.name || member.email}</span>
                <span className={styles.role}>{member.role}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </AsyncView>
  );
}

export function App({ user: initial }: { user: User | null }) {
  const [user, setUser] = useState<User | null>(initial);
  const path = usePath();
  const match = useMemo(() => ROUTES.match(path), [path]);

  if (!user) {
    return (
      <main className={styles.content}>
        <SignIn onDone={setUser} />
      </main>
    );
  }

  async function signOut() {
    await del('/api/auth/session');
    setUser(null);
    navigate('/');
  }

  let view: preact.ComponentChildren;
  if (!match) view = <EmptyState title="No such page" hint={path} />;
  else if (match.pattern === '/projects/{id}') view = <ProjectDetailView id={match.params.id ?? ''} />;
  else if (match.pattern === '/settings') view = <EmptyState title="Settings" hint="TODO(design)" />;
  else view = <ProjectList />;

  return (
    <div className={styles.shell}>
      <header className={styles.masthead}>
        <strong>yeaboi</strong>
        <span className={styles.role}>{user.email}</span>
        <button type="button" className={styles.action} onClick={signOut}>
          Sign out
        </button>
      </header>
      <nav className={styles.rail} aria-label="Sections">
        {RAIL.map((item) => (
          <a
            key={item.href}
            className={styles.railLink}
            href={item.href}
            aria-current={path.startsWith(item.href) ? 'page' : undefined}
          >
            {item.label}
          </a>
        ))}
      </nav>
      <main className={styles.content}>{view}</main>
      <footer className={styles.footer}>
        <Credit>Built with yeaboi.ai</Credit>
      </footer>
    </div>
  );
}
