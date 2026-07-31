/**
 * Entry for the share gate — the page a teammate lands on when they open a
 * tunnel link before they have a token (`sharing/server.py`).
 *
 * The first React consumer in the codebase, chosen deliberately: it is the
 * smallest real page and it runs under the strictest CSP yeaboi serves
 * (`default-src 'none'`, no eval, no external origin), so if the inlined IIFE
 * boots here it will boot anywhere.
 */
import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import { applyStoredTheme } from '../runtime/theme';
import { JoinGate } from '../shared/JoinGate';
import './page.css';

// Before the first paint, so the gate matches the palette the visitor picked on
// the last yeaboi page they opened rather than flashing midnight at them.
applyStoredTheme();

const root = document.getElementById('root');
// Missing root means the Python side changed `root_id` — loud, not silent, and
// visible in the tunnel's browser console rather than as a blank page.
if (!root) throw new Error('gate: #root is missing from the document');

// Replaces the server-rendered <noscript> shell that render_page put here.
createRoot(root).render(<JoinGate />);
