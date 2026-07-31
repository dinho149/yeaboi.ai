/**
 * Entry for the reporting slide deck.
 *
 * The palette is applied here rather than waiting for React, for the same
 * reason the boards apply their theme before mounting: the document is one
 * file, the script is inline at the end of it, and a browser is free to paint
 * the body before running it. A slide deck opening on the wrong background and
 * then correcting itself is a flash people notice, because the first thing they
 * do with a deck is put it on a projector.
 */
import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import './deck.css';
import { applyStoredTheme } from '../runtime/theme';
import { App } from './App';
import { readDeckBoot } from './boot';
import { applyPalette } from './palette';

const boot = readDeckBoot();

// The site palette first, then the deck's accents on top of it. This call is
// what the deck was missing: it was the one surface that never read the
// visitor's stored theme, so someone who had chosen `light` everywhere else
// still got a dark deck with no way back. Order matters — `applyPalette` reads
// the resolved `--bg` to decide whether an accent needs darkening to stay
// legible, so the theme has to be on the document first.
applyStoredTheme();

const palette = boot.palettes[boot.theme];
if (palette) applyPalette(boot.theme, palette);

const root = document.getElementById('root');
if (!root) throw new Error('deck: #root is missing from the document');

createRoot(root).render(<App boot={boot} />);
