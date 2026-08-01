/**
 * Wrap a rendered region so it can be corrected — when the document is served
 * editable and the payload told us where the field lives.
 *
 * Returns the children untouched otherwise, which is every file on disk. Shared
 * by the report components rather than repeated in each, so adopting editing in
 * one is a one-line change at each call site instead of a branch.
 *
 * It lives beside the primitives and not in `design/` or `shared/`: those are
 * imported by all five bundles, and the edit stack has no business in the four
 * that never render a report.
 */

import type { ComponentChildren } from 'preact';

import type { EditMap } from '../boot';
import { Editable } from './Editable';

export function Field({
  edit,
  field,
  label,
  children,
}: {
  /** The payload node's edit map, or undefined on a file export. */
  edit: EditMap | undefined;
  /** The artifact field name — the key the server put in that map. */
  field: string;
  /** Accessible name, composed at the call site: "Ada's blocker", not "blocker". */
  label: string;
  children: ComponentChildren;
}) {
  const target = edit?.[field];
  if (!target) return <>{children}</>;
  return (
    <Editable path={target.path} label={label} value={target.value}>
      {children}
    </Editable>
  );
}
