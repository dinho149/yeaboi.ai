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
 * Every export is a payload page now, so this mounts unconditionally. The
 * branch that used to sit here existed for the string-templated pages
 * `html_theme.html_page` wrote, which carried no `#root` and needed only the
 * theme button; it went when the last exporter did.
 */

import { createRoot } from 'react-dom/client';

import '../design/tokens.css';
import { applyStoredTheme } from '../runtime/theme';
import { readExportBoot } from './boot';
import { EditApp } from './EditApp';
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

  // The one branch in the bundle. `editing` is present only on a document
  // served by the share server; a file on disk does not have the key, so this
  // never reaches the edit stack and the file executes no network code.
  createRoot(root).render(
    boot.editing ? (
      <EditApp chrome={boot.chrome} report={boot.report} editing={boot.editing} theme={theme} />
    ) : (
      <Shell chrome={boot.chrome} theme={theme}>
        <Report report={boot.report} />
      </Shell>
    )
  );
}
