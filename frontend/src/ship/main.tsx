/**
 * Entry for the ship board.
 *
 * `data-mode="ship"` is set on `<html>` by the Python side, which layers the
 * ship accent over the visitor's chosen palette (see tokens.css).
 */
import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import { applyStoredTheme, THEME_KEYS } from '../runtime/theme';
import { App } from './App';
import { readShipBoot } from './boot';

applyStoredTheme(THEME_KEYS.site);

const root = document.getElementById('root');
if (!root) throw new Error('ship: #root is missing from the document');

createRoot(root).render(<App boot={readShipBoot()} />);
