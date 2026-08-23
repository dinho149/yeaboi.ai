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
import { Home } from './pages/Home';
import { Usage } from './pages/Usage';
import { WhatsNew } from './pages/WhatsNew';
import { APP_ROUTES, DEFAULT_ROUTE, routeFor } from './routes';

const PAGES: Record<string, ComponentType> = {
  '/home': Home,
  '/whats-new': WhatsNew,
  '/usage': Usage,
};

function useHashRoute(): string {
  const [path, setPath] = useState(() => window.location.hash.slice(1) || DEFAULT_ROUTE);
  useEffect(() => {
    const onChange = () => setPath(window.location.hash.slice(1) || DEFAULT_ROUTE);
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
  return (
    <nav class="sidebar">
      <div class="sidebar-brand">
        <Duck state="idle" size={36} />
        <Wordmark text="YEABOI" label="yeaboi" size="110px" />
      </div>
      {APP_ROUTES.map((route) => (
        <a key={route.path} href={`#${route.path}`} aria-current={active === route.path ? 'page' : undefined}>
          {route.title}
        </a>
      ))}
      <div class="sidebar-footer">
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
