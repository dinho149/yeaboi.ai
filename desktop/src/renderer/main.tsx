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
import { Agents } from './pages/Agents';
import { Analysis } from './pages/Analysis';
import { AnalysisResults } from './pages/AnalysisResults';
import { AnalysisSetup } from './pages/AnalysisSetup';
import { Ceremonies } from './pages/Ceremonies';
import { CeremoniesSlack } from './pages/CeremoniesSlack';
import { Chat } from './pages/Chat';
import { Home } from './pages/Home';
import { Performance } from './pages/Performance';
import { PerformanceEngineer } from './pages/PerformanceEngineer';
import { Planning } from './pages/Planning';
import { Poker } from './pages/Poker';
import { Provenance } from './pages/Provenance';
import { PokerBoard } from './pages/PokerBoard';
import { PokerSetup } from './pages/PokerSetup';
import { Reporting } from './pages/Reporting';
import { ReportingSetup } from './pages/ReportingSetup';
import { ReportingStyle } from './pages/ReportingStyle';
import { Retro } from './pages/Retro';
import { RetroBoard } from './pages/RetroBoard';
import { Roadmap } from './pages/Roadmap';
import { Sessions } from './pages/Sessions';
import { Settings } from './pages/Settings';
import { Ship } from './pages/Ship';
import { ShipRun } from './pages/ShipRun';
import { Setup } from './pages/Setup';
import { Standup } from './pages/Standup';
import { StandupReview } from './pages/StandupReview';
import { StandupSchedule } from './pages/StandupSchedule';
import { StandupSetup } from './pages/StandupSetup';
import { Usage } from './pages/Usage';
import { WhatsNew } from './pages/WhatsNew';
import { APP_ROUTES, DEFAULT_ROUTE, routeFor } from './routes';

const PAGES: Record<string, ComponentType> = {
  '/home': Home,
  '/whats-new': WhatsNew,
  '/humans/planning': Planning,
  '/humans/planning/chat': Chat,
  '/humans/planning/sessions': Sessions,
  '/humans/planning/roadmap': Roadmap,
  '/humans/analysis': Analysis,
  '/humans/analysis/new': AnalysisSetup,
  '/humans/analysis/results': AnalysisResults,
  '/humans/standup': Standup,
  '/humans/standup/setup': StandupSetup,
  '/humans/standup/schedule': StandupSchedule,
  '/humans/standup/review': StandupReview,
  '/humans/retro': Retro,
  '/humans/retro/board': RetroBoard,
  '/humans/poker': Poker,
  '/humans/poker/new': PokerSetup,
  '/humans/poker/board': PokerBoard,
  '/humans/performance': Performance,
  '/humans/performance/engineer': PerformanceEngineer,
  '/humans/reporting': Reporting,
  '/humans/reporting/new': ReportingSetup,
  '/humans/reporting/style': ReportingStyle,
  '/humans/ship': Ship,
  '/humans/ship/run': ShipRun,
  // One page over four modes — it reads its kind from the hash.
  '/agents/usage': Agents,
  '/agents/advisor': Agents,
  '/agents/standup': Agents,
  '/agents/security': Agents,
  '/ceremonies': Ceremonies,
  '/ceremonies/slack': CeremoniesSlack,
  '/provenance': Provenance,
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

/**
 * Which sidebar group a route belongs to, or null when it is not a destination.
 *
 * Three things are deliberately not in the nav: a mode's sub-pages (the chat
 * and saved plans off Planning, the stepper off Analysis, the Slack tab off
 * Ceremonies), the settings and setup routes (they live in the footer like the
 * TUI's secondary row), and the registry's non-route affordances —
 * `dialog:share`, `action:anonymize` — which are parity entries for buttons.
 */
function navGroup(path: string): string | null {
  if (!path.startsWith('/') || path.startsWith('/settings/') || path === '/setup') return null;
  if (/^\/humans\/[^/]+$/.test(path)) return 'Humans';
  if (/^\/agents\/[^/]+$/.test(path)) return 'Agents';
  if (path === '/ceremonies' || path === '/provenance') return 'Ops';
  return /^\/[^/]+$/.test(path) ? '' : null;
}

const NAV_GROUPS = ['', 'Humans', 'Agents', 'Ops'] as const;

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
  // The three /settings/* routes collapse into one footer entry — the page owns
  // its own tab bar. Everything else is grouped by navGroup above.
  return (
    <nav class="sidebar">
      <div class="sidebar-brand">
        <Duck state="idle" size={36} />
        <Wordmark text="YEABOI" label="yeaboi" size="110px" />
      </div>
      {NAV_GROUPS.map((group) => {
        const routes = APP_ROUTES.filter((route) => navGroup(route.path) === group);
        if (routes.length === 0) return null;
        return (
          <div key={group || 'top'} class="sidebar-section">
            {group && <div class="sidebar-heading">{group}</div>}
            {routes.map((route) => (
              <a
                key={route.path}
                class="sidebar-item"
                href={`#${route.path}`}
                aria-current={
                  active === route.path || active.startsWith(`${route.path}/`) ? 'page' : undefined
                }
              >
                {route.title}
              </a>
            ))}
          </div>
        );
      })}
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
