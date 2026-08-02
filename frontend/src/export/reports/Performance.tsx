/**
 * The performance exports: 1:1 prep, 1:1 summary, and the six-month review.
 *
 * One component for all three. That is the point of the payload shape rather
 * than an accident of it — `build_prep_html`, `build_completion_html` and
 * `build_review_html` were three near-identical functions differing only in
 * which section titles they passed, and each one had a Markdown twin that
 * differed the same way. There is one renderer here and one payload builder in
 * Python, and the artifact-specific part is now a list of `(title, items)`
 * pairs, which is what it always was.
 *
 * The engineer's name is a row in the body rather than the page title alone,
 * with an avatar: these documents get read in a stack, one per person, and the
 * name is the first thing you need to be sure about.
 */

import { Avatar, NoticeBlock, Prose, proseBullets } from '../../design/primitives';
import type { EditMap, PerfSection } from '../boot';
import { EditableSlot } from '../editing/Editable';
import { Field } from '../editing/Field';
import styles from './reports.module.css';

/** Long enough that it is packed prose, not a bullet someone wrote. */
const SPLIT_OVER = 160;

function sectionId(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function Section({ section }: { section: PerfSection }) {
  if (!section.items.length) return null;
  return (
    <section id={sectionId(section.title)}>
      <h2 className={styles['h2']}>{section.title}</h2>
      <ul className={styles['bullets']}>
        {section.items.flatMap((item, i) =>
          // A long item is one sentence carrying three facts; splitting it is
          // the difference between a scannable list and a wall. A short one is
          // already a bullet, and splitting it would fragment a written thought.
          // Flattened into this list rather than nesting a second <ul>, which
          // is invalid outside an <li> and indents for no reason.
          (item.length > SPLIT_OVER ? proseBullets(item) : [item]).map((fragment, j) => (
            <li key={`${i}-${j}`}>{fragment}</li>
          ))
        )}
      </ul>
    </section>
  );
}

export function Performance({
  engineer,
  lead,
  edit,
  sections,
  footnote,
  warnings,
}: {
  engineer: string;
  lead?: { title: string; text: string; field?: string };
  edit?: EditMap;
  sections: PerfSection[];
  footnote?: string;
  warnings: string[];
}) {
  return (
    <>
      {engineer ? (
        <p className={styles['identity']}>
          <Avatar name={engineer} />
          <strong>{engineer}</strong>
        </p>
      ) : null}

      <EditableSlot anchor="" label="this review" />
      {lead ? (
        <section id={sectionId(lead.title)}>
          <h2 className={styles['h2']}>{lead.title}</h2>
          <Field edit={edit} field={lead.field ?? ''} label={lead.title.toLowerCase()}>
            <Prose text={lead.text} />
          </Field>
        </section>
      ) : null}

      {sections.map((section) => (
        <Section key={section.title} section={section} />
      ))}

      {footnote ? <p className={styles['footnote']}>{footnote}</p> : null}
      <NoticeBlock title="Notices" items={warnings} />
    </>
  );
}
