/**
 * Entry for every static exported report.
 *
 * One bundle for all of them rather than one per report. The alternative —
 * an entry each — would keep every file lean but ship a separate copy of the
 * framework in each of ten committed bundles, which is a worse trade for a
 * repository than ~30 KB of unused renderers is for a file that is emailed
 * once. It is also what makes the shell, the primitives and the Markdown
 * reader genuinely shared rather than shared-by-convention.
 *
 * ## The mixed state during the migration
 *
 * Exports move to React in three commits, so for two of them this same bundle
 * is loaded by two kinds of page: the new ones, which carry a boot payload and
 * a `#root`, and the ones `html_theme.html_page` still writes as markup
 * strings, which carry neither. The legacy pages need exactly one thing from
 * this file — the theme button their header wires to `__yeaboiCycleTheme()` —
 * so the fallback branch below is that, and it deletes with `html_page`.
 */

import { createRoot } from 'react-dom/client';

import '../design/export.css';
import { applyStoredTheme, installThemeSwitcher } from '../runtime/theme';
import { readExportBoot } from './boot';
import { Report } from './Report';
import { Shell } from './Shell';

const root = document.getElementById('root');

if (root) {
  // Before mount, for the same reason the deck does it: the document is one
  // file with its script at the end, and a browser may paint the body first. A
  // report that flashes midnight at someone who chose light is a flash they
  // see every single time they open one.
  // `data-mode` is not set here: the server writes it onto <html>, so the
  // accent is right in the very first paint rather than after this script runs.
  const theme = applyStoredTheme();
  const boot = readExportBoot();

  createRoot(root).render(
    <Shell chrome={boot.chrome} theme={theme}>
      <Report report={boot.report} />
    </Shell>
  );
} else {
  installThemeSwitcher();
}
