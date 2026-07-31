/**
 * Entry for the planning-poker board.
 *
 * `data-mode="poker"` is set on `<html>` by the Python side, which layers the
 * gold accent over whichever palette the visitor chose. The theme itself is
 * applied by the app, because the host can cast one mid-session.
 */
import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import { applyStoredTheme, THEME_KEYS } from '../runtime/theme';
import { App } from './App';
import { readPokerBoot } from './boot';

// Before the first paint, so a reload does not flash midnight at someone who
// has been sitting in solarized all meeting.
applyStoredTheme(THEME_KEYS.site);

const root = document.getElementById('root');
if (!root) throw new Error('poker: #root is missing from the document');

createRoot(root).render(<App boot={readPokerBoot()} />);
