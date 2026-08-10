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
import { flushSync } from 'preact/compat';
import { Button, Field, Input, ToastRegion, useToasts } from '../design/primitives';
import { ArtifactView } from './Artifact';
import { ImportDialog } from './Import';
import { RoomList } from './Rooms';
import { Settings } from './Settings';
import { Credit } from '../shared/Credit';
import { del, get, post } from './api';
import { interceptLinks, navigate, Routes } from './router';
import { AsyncView, EmptyState, Loading } from './Slots';
import { useEnterList, useFlip } from './useMotion';
import { useAsync } from './useAsync';
import type { ArtifactSummary, ProjectDetail, ProjectSummary, User } from './types';
import styles from './app.module.css';

const ROUTES = new Routes([
  '/',
  '/projects',
  '/projects/{id}',
  '/projects/{id}/artifacts/{artifactId}',
  '/settings',
]);

/** Modes, in the order the TUI lists them. */
const RAIL = [
  { href: '/projects', label: 'Projects' },
  { href: '/settings', label: 'Settings' },
];

function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  const flip = useFlip();

  useEffect(() => {
    // The route change is the mutation FLIP brackets: capture where the shared
    // elements are, swap the view, then play them to their new positions.
    // `flushSync` is what makes the swap synchronous — without it Preact
    // batches the re-render and Flip.from measures the OLD layout, producing a
    // tween from a position to itself.
    const sync = () => flip(() => flushSync(() => setPath(window.location.pathname)));
    window.addEventListener('popstate', sync);
    const release = interceptLinks(sync);
    return () => {
      window.removeEventListener('popstate', sync);
      release();
    };
  }, [flip]);
  return path;
}

function SignIn({ onDone }: { onDone: (user: User) => void }) {
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  // null while unknown: rendering the email form and then swapping it for the
  // claim form a moment later is worse than waiting one request.
  const [claimable, setClaimable] = useState<boolean | null>(null);

  useEffect(() => {
    void get<{ available: boolean }>('/api/auth/first-run').then((result) => {
      setClaimable(result.ok ? result.data.available : false);
    });
  }, []);

  // A link lands here as /signin?token=... Redeem it once, on arrival.
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) return;
    setBusy(true);
    void post<{ user: User }>('/api/auth/session', { token }).then((result) => {
      setBusy(false);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      // Drop the token from the address bar before anything else can read it:
      // it is spent, but a one-time credential should not sit in history or
      // ride along in a Referer.
      navigate('/projects', { replace: true });
      onDone(result.data.user);
    });
  }, [onDone]);

  async function submit(event: Event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    // On a fresh local instance there is no mail server, so the first account
    // is claimed directly. Everywhere else a link is sent.
    const result = claimable
      ? await post<{ user: User }>('/api/auth/claim', { email })
      : await post('/api/auth/request', { email });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (claimable) {
      onDone((result.data as { user: User }).user);
      navigate('/projects');
      return;
    }
    setSent(true);
  }

  if (claimable === null) {
    return <Loading label="Starting" />;
  }

  if (sent) {
    return (
      <EmptyState
        title="Check your email"
        hint={`If ${email} has access, a sign-in link is on its way. It expires in 15 minutes.`}
      />
    );
  }

  return (
    <form className={styles.signin} onSubmit={submit}>
      <Field label="Email" error={error} required>
        {(props) => (
          <Input
            {...props}
            type="email"
            value={email}
            onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
          />
        )}
      </Field>
      <Button type="submit" tone="primary" disabled={busy} aria-busy={busy || undefined}>
        {claimable ? 'Get started' : 'Send a sign-in link'}
      </Button>
    </form>
  );
}

function ProjectList({ notify }: { notify: (message: string) => void }) {
  const [importing, setImporting] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  // Keyed on the state's status so the stagger runs when the rows arrive,
  // not on the loading pass when there is nothing to animate.
  const listRef = useEnterList<HTMLUListElement>('li', 'projects');
  const state = useAsync(
    () => get<{ projects: ProjectSummary[] }>('/api/projects'),
    [reloadKey],
    { isEmpty: (data) => data.projects.length === 0 },
  );

  async function create() {
    // TODO(design): a prompt() is a placeholder for a real create dialog —
    // <Modal> exists for it, but what the flow asks for is a product decision.
    const name = window.prompt('Project name');
    if (!name) return;
    const result = await post('/api/projects', { name });
    if (!result.ok) {
      notify(result.error);
      return;
    }
    notify(`Created ${name}`);
    navigate('/projects');
  }

  return (
    <>
    <ImportDialog
      open={importing}
      onClose={() => setImporting(false)}
      onImported={() => setReloadKey((n) => n + 1)}
      notify={notify}
    />
    <AsyncView
      state={state}
      empty={
        <EmptyState
          title="No projects yet"
          hint="A project holds the plans, standups and retros for one team."
          action={
            <>
              <Button tone="primary" onClick={create}>
                New project
              </Button>
              <Button onClick={() => setImporting(true)}>Import from the terminal app</Button>
            </>
          }
        />
      }
    >
      {(data) => (
        <>
        <div className={styles.toolbar}>
          <Button tone="primary" size="s" onClick={create}>
            New project
          </Button>
          <Button size="s" onClick={() => setImporting(true)}>
            Import
          </Button>
        </div>
        <ul className={styles.projectList} ref={listRef}>
          {data.projects.map((project) => (
            <li key={project.id} className={styles.projectRow}>
              {/* The flip id is what makes this row and the detail heading the
                  same object to the motion layer. Same id on both screens. */}
              <a
                className={styles.projectName}
                href={`/projects/${project.id}`}
                data-flip-id={`project-${project.id}`}
              >
                {project.name}
              </a>
              <span className={styles.role}>{project.role}</span>
            </li>
          ))}
        </ul>
        </>
      )}
    </AsyncView>
    </>
  );
}

function ArtifactList({ projectId }: { projectId: string }) {
  const state = useAsync(
    () => get<{ artifacts: ArtifactSummary[] }>(`/api/projects/${projectId}/artifacts`),
    [projectId],
    { isEmpty: (data) => data.artifacts.length === 0 },
  );
  return (
    <AsyncView
      state={state}
      empty={
        <EmptyState
          title="No documents yet"
          hint="Plans, standups, retros and reports for this project will appear here."
        />
      }
    >
      {(data) => (
        <ul className={styles.projectList}>
          {data.artifacts.map((artifact) => (
            <li key={artifact.id} className={styles.projectRow}>
              <a className={styles.projectName} href={`/projects/${projectId}/artifacts/${artifact.id}`}>
                {artifact.title || artifact.kind}
              </a>
              <span className={styles.role}>{artifact.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </AsyncView>
  );
}

function ProjectDetailView({ id, notify }: { id: string; notify: (message: string) => void }) {
  const state = useAsync(() => get<ProjectDetail>(`/api/projects/${id}`), [id]);
  return (
    <AsyncView state={state} empty={<EmptyState title="Nothing here" />}>
      {(project) => (
        <section>
          <h1 data-flip-id={`project-${project.id}`}>{project.name}</h1>
          <RoomList projectId={project.id} notify={notify} />
          <ArtifactList projectId={project.id} />
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
  const { toasts, push, dismiss } = useToasts();
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
  else if (match.pattern === '/projects/{id}/artifacts/{artifactId}')
    view = <ArtifactView id={match.params.artifactId ?? ''} />;
  else if (match.pattern === '/projects/{id}')
    view = <ProjectDetailView id={match.params.id ?? ''} notify={push} />;
  else if (match.pattern === '/settings')
    view = (
      <Settings
        user={user}
        notify={push}
        onSignedOut={() => {
          setUser(null);
          navigate('/');
        }}
      />
    );
  else view = <ProjectList notify={push} />;

  return (
    <div className={styles.shell}>
      <header className={styles.masthead}>
        <strong>yeaboi</strong>
        <span className={styles.role}>{user.email}</span>
        <Button shape="bare" size="s" onClick={signOut}>
          Sign out
        </Button>
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
      <ToastRegion toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
