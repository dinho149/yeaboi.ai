/** Mount the app. Boot payload carries the signed-in user, or null. */

import { render } from 'preact';
import { App } from './App';
import type { User } from './types';
import '../design/tokens.css';
import './app.module.css';

interface Boot {
  user: User | null;
}

function boot(): Boot {
  const island = document.getElementById('yeaboi-data');
  if (!island?.textContent) return { user: null };
  try {
    // textContent + JSON.parse, never eval - the CSP forbids it and this is
    // the same contract every other surface boots under.
    return JSON.parse(island.textContent) as Boot;
  } catch {
    return { user: null };
  }
}

const root = document.getElementById('root');
if (root) render(<App user={boot().user} />, root);
