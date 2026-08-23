// The desktop shell: hash router + sidebar + splash-until-backend-ready.
// Pages live in pages/; the route table is routes.ts (the parity source).

// tokens.css @imports palette.css and fonts.css — one import, whole system.
import '@design/tokens.css';
import './app.css';

import { Duck } from '@design/primitives/Duck';
import { Wordmark } from '@design/primitives/Wordmark';
import { type ComponentType, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { getBackendState, onBackendState } from './api';
import { Chat } from './pages/Chat';
import { Home } from './pages/Home';
import { Planning } from './pages/Planning';
import { Sessions } from './pages/Sessions';
import { Settings } from './pages/Settings';
import { Setup } from './pages/Setup';
import { Usage } from './pages/Usage';
import { WhatsNew } from './pages/WhatsNew';
import { APP_ROUTES, DEFAULT_ROUTE, routeFor } from './routes';

const PAGES: Record<string, ComponentType> = {
  '/home': Home,
  '/whats-new': WhatsNew,
  '/humans/planning': Planning,
  '/humans/planning/chat': Chat,
  '/humans/planning/sessions': Sessions,
  '/usage': Usage,
  '/settings/credentials': Settings,
  '/settings/sharing': Settings,
  '/settings/system': Settings,
  '/setup': Setup,
};

/** The route half of a hash — anything after `?` is the page's own business. */
function pathOf(hash: string): string {
  const path = hash.slice(1).split('?')[0] ?? '';
  return path || DEFAULT_ROUTE;
}

function useHashRoute(): string {
  const [path, setPath] = useState(() => pathOf(window.location.hash));
  useEffect(() => {
    const onChange = () => setPath(pathOf(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return routeFor(path) ? path : DEFAULT_ROUTE;
}

type Backend = { kind: 'starting' } | { kind: 'ready' } | { kind: 'down'; reason?: string };

function Splash({ backend }: { backend: Backend }) {
  return (
    <div class="splash">
      <div class={backend.kind === 'starting' ? 'duck-hop' : ''}>
        <Duck state={backend.kind === 'down' ? 'offline' : 'idle'} size={72} />
      </div>
      <Wordmark text="YEABOI" label="yeaboi" size="200px" />
      {backend.kind === 'starting' && <div class="status">starting…</div>}
      {backend.kind === 'down' && (
        <div class="error-box">
          <strong>The backend is not running.</strong>
          <p class="reason">{backend.reason ?? 'unknown reason'}</p>
        </div>
      )}
    </div>
  );
}

function Sidebar({ active }: { active: string }) {
  // The three /settings/* routes collapse into one nav entry (the page owns
  // its own tab bar), and Setup + Settings live in the footer like the TUI's
  // secondary row rather than among the modes.
  // The chat and the saved-plans list hang off Planning rather than standing
  // in the nav; Setup and Settings live in the footer like the TUI's secondary
  // row rather than among the modes.
  const primary = APP_ROUTES.filter(
    (route) =>
      !route.path.startsWith('/settings/') &&
      route.path !== '/setup' &&
      !route.path.startsWith('/humans/planning/'),
  );
  return (
    <nav class="sidebar">
      <div class="sidebar-brand">
        <Duck state="idle" size={36} />
        <Wordmark text="YEABOI" label="yeaboi" size="110px" />
      </div>
      {primary.map((route) => (
        <a
          key={route.path}
          href={`#${route.path}`}
          aria-current={active === route.path || active.startsWith(`${route.path}/`) ? 'page' : undefined}
        >
          {route.title}
        </a>
      ))}
      <div class="sidebar-footer">
        <a href="#/setup" aria-current={active === '/setup' ? 'page' : undefined}>
          Setup
        </a>
        <a href="#/settings/credentials" aria-current={active.startsWith('/settings/') ? 'page' : undefined}>
          Settings
        </a>
        <div class="sidebar-version">yeaboi desktop</div>
      </div>
    </nav>
  );
}

function App() {
  const path = useHashRoute();
  const [backend, setBackend] = useState<Backend>({ kind: 'starting' });

  useEffect(() => {
    // Pull once (the backend may have become ready before this window
    // subscribed), then follow transitions.
    void getBackendState().then((state) => setBackend(state as Backend));
    onBackendState((state) => setBackend(state as Backend));
  }, []);

  if (backend.kind !== 'ready') return <Splash backend={backend} />;

  const Page = PAGES[path] ?? Home;
  return (
    <div class="shell">
      <Sidebar active={path} />
      <main class="content">
        <Page />
      </main>
    </div>
  );
}

const app = document.getElementById('app');
if (!app) throw new Error('desktop: #app is missing from the document');
createRoot(app).render(<App />);
