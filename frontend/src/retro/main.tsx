/**
 * Entry for the retro board.
 *
 * `data-mode="retro"` is set on `<html>` by the Python side, which is what
 * layers the mode accent over the chosen palette (see tokens.css). The theme
 * itself is applied by the app rather than here, because the host can cast one
 * mid-ceremony and the app owns that state.
 */
import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import { applyStoredTheme, THEME_KEYS } from '../runtime/theme';
import { App } from './App';
import { readRetroBoot } from './boot';

// Before the first paint, so a reload does not flash midnight at someone who
// has been sitting in solarized for the last half hour.
applyStoredTheme(THEME_KEYS.retro);

const root = document.getElementById('root');
if (!root) throw new Error('retro: #root is missing from the document');

createRoot(root).render(<App boot={readRetroBoot()} />);
