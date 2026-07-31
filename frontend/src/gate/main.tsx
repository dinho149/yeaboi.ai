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
import { readGateBoot } from './boot';
import './page.css';

const boot = readGateBoot();

// Python already wrote `data-mode` onto <html>, so the accent is correct before
// a single byte of this script ran. This is the belt to that braces: a browser
// showing a cached copy of a document from before the gate was branded has the
// island but not the attribute, and would otherwise wear the base accent while
// the wordmark said "standup".
if (boot?.mode) document.documentElement.setAttribute('data-mode', boot.mode);

// Before the first paint, so the gate matches the palette the visitor picked on
// the last yeaboi page they opened rather than flashing midnight at them.
applyStoredTheme();

const root = document.getElementById('root');
// Missing root means the Python side changed `root_id` — loud, not silent, and
// visible in the tunnel's browser console rather than as a blank page.
if (!root) throw new Error('gate: #root is missing from the document');

// Replaces the server-rendered <noscript> shell that render_page put here.
// Every prop is optional: with no island JoinGate falls back to the neutral
// wordmark and copy it has always had.
createRoot(root).render(
  <JoinGate
    {...(boot?.wordmark ? { wordmark: boot.wordmark } : {})}
    {...(boot?.frameTitle ? { frameTitle: boot.frameTitle } : {})}
    {...(boot?.heading ? { heading: boot.heading } : {})}
    {...(boot?.eyebrow ? { eyebrow: boot.eyebrow } : {})}
    {...(boot?.cta ? { cta: boot.cta } : {})}
  />,
);
