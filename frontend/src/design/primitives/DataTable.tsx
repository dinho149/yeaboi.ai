/**
 * A tabular data grid.
 *
 * Port of `html_theme`'s `.data-table` markup, generic over the row type so a
 * column declares how to render its cell instead of the caller pre-stringifying
 * everything.
 *
 * Two things it does that the string version could not:
 *
 * * The **wrapper** scrolls horizontally, not the page. A wide table on a phone
 *   otherwise makes the whole document scroll sideways, which breaks every
 *   vertical swipe over it.
 * * A `numeric` column gets the mono voice and right alignment automatically,
 *   so a column of counts is actually comparable down the page.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface Column<Row> {
  /** Stable identity for the column. Also the default header text. */
  key: string;
  header?: ReactNode;
  cell(row: Row, index: number): ReactNode;
  /** Right-align and render in the mono voice. */
  numeric?: boolean;
  width?: string;
}

export interface DataTableProps<Row> {
  rows: readonly Row[];
  columns: readonly Column<Row>[];
  /** Stable row identity. Falls back to the index for genuinely static tables. */
  rowKey?(row: Row, index: number): string;
  /** Shown instead of an empty table body. */
  empty?: ReactNode;
  /** Describes the table for screen readers. Rendered visually as a caption. */
  caption?: string;
  className?: string | undefined;
}

export function DataTable<Row>({
  rows,
  columns,
  rowKey,
  empty,
  caption,
  className,
}: DataTableProps<Row>) {
  if (!rows.length && empty !== undefined) return <>{empty}</>;

  return (
    <div className={styles['tableWrap']}>
      <table className={cx(styles['table'], className)}>
        {caption ? <caption>{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cx(column.numeric && styles['numeric'])}
                style={column.width ? { width: column.width } : undefined}
              >
                {column.header ?? column.key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey ? rowKey(row, index) : index}>
              {columns.map((column) => (
                <td key={column.key} className={cx(column.numeric && styles['numeric'])}>
                  {column.cell(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
