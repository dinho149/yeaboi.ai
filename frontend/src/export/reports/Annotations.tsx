/**
 * What readers added to a generated report.
 *
 * One component for every report, drawn by `Report.tsx` after whichever one it
 * dispatched to. The alternative — a section per report component — is nine
 * copies of the same list, and the failure mode of the ninth being forgotten is
 * a note that a reader wrote, the server stored, and nobody ever sees.
 *
 * **Attribution here is self-declared.** Whoever held the link typed their own
 * name. So this draws the name and nothing that would imply it was checked: no
 * verified badge, no lock, and the section heading says who added it rather
 * than calling it a record.
 */

import type { AnnotationRow } from '../boot';
import { formatPath } from '../editing/paths';
import styles from './reports.module.css';

/** Matches `artifacts/render.NOTES_HEADING`; a test pins the two together. */
const HEADING = 'Added by the team';

function When({ at }: { at: string }) {
  if (!at) return null;
  // Sliced rather than parsed: the value is an ISO-8601 string produced by the
  // server, and a Date here would re-render it in the reader's timezone — so an
  // export opened in Sydney would disagree with the one opened in London about
  // what day a note was written.
  return <span className={styles['annotationWhen']}>{at.slice(0, 10)}</span>;
}

export function Annotations({ rows }: { rows: readonly AnnotationRow[] }) {
  const kept = rows.filter((row) => row.text);
  if (!kept.length) return null;

  return (
    <section className={styles['annotations']} id="annotations">
      <h2>{HEADING}</h2>
      <ul className={styles['annotationList']}>
        {kept.map((row, i) => (
          <li key={`${row.anchor}-${i}`} className={styles['annotation']}>
            <p className={styles['annotationBody']}>
              {row.kind === 'field' && row.label ? <strong>{row.label}: </strong> : null}
              {row.text}
            </p>
            <p className={styles['annotationMeta']}>
              {/* Space inside the expression — JSX strips it before a newline. */}
              {row.avatar ? <span aria-hidden="true">{`${row.avatar} `}</span> : null}
              {row.author ? <span>{row.author}</span> : <span>Anonymous</span>}
              {/* Where it was left. The Markdown twin says this too, and a note
                  anchored to one member reading as a document-level note is a
                  real difference in what the document claims. */}
              {row.anchor ? <span className={styles['annotationWhere']}>on {formatPath(row.anchor)}</span> : null}
              <When at={row.at} />
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
