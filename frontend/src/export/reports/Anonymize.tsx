/**
 * The anonymize export: somebody else's report, masked and re-published.
 *
 * The only export whose content is a document rather than a data structure, so
 * it is the only one that renders {@link readMarkdown}'s blocks. Everything
 * here is a plain element around plain text — there is no
 * `dangerouslySetInnerHTML` on this page, which matters more here than
 * anywhere else in the product: the input is a document that was *specifically
 * handled because it contained sensitive material*, and it has passed through
 * an LLM.
 */

import { DataTable, NoticeBlock, RichText, type Run } from '../../design/primitives';
import type { MdBlock } from '../markdown';
import { readMarkdown } from '../markdown';
import styles from './reports.module.css';

function Heading({ level, runs }: { level: number; runs: Run[] }) {
  // The page already owns <h1>; a document heading starts at <h2> so the
  // outline stays walkable. Levels past <h6> flatten rather than emitting an
  // <h7>, which is not an element.
  const Tag = `h${Math.min(6, level + 1)}` as 'h2';
  return (
    <Tag>
      <RichText runs={runs} />
    </Tag>
  );
}

function Block({ block }: { block: MdBlock }) {
  switch (block.t) {
    case 'h':
      return <Heading level={block.level} runs={block.runs} />;
    case 'p':
      return (
        <p>
          <RichText runs={block.runs} />
        </p>
      );
    case 'quote':
      return (
        <blockquote className={styles['quote']}>
          <RichText runs={block.runs} />
        </blockquote>
      );
    case 'code':
      return (
        <pre className={styles['code']}>
          <code>{block.text}</code>
        </pre>
      );
    case 'rule':
      return <hr className={styles['rule']} />;
    case 'list':
      return block.ordered ? (
        <ol>
          {block.items.map((runs, i) => (
            <li key={i}>
              <RichText runs={runs} />
            </li>
          ))}
        </ol>
      ) : (
        <ul>
          {block.items.map((runs, i) => (
            <li key={i}>
              <RichText runs={runs} />
            </li>
          ))}
        </ul>
      );
    case 'table':
      return (
        <DataTable
          rows={block.rows}
          columns={block.head.map((head, i) => ({
            key: String(i),
            header: <RichText runs={head} />,
            cell: (row: Run[][]) => <RichText runs={row[i] ?? []} />,
          }))}
        />
      );
  }
}

export function Anonymize({ markdown, warnings }: { markdown: string; warnings: string[] }) {
  const blocks = readMarkdown(markdown);
  return (
    <>
      <article className={styles['document']}>
        {blocks.map((block, i) => (
          // Index keys: a rendered document is static and rebuilt whole, and
          // the blocks carry no identity of their own to key on.
          <Block key={i} block={block} />
        ))}
      </article>
      <NoticeBlock title="Notices" items={warnings} />
    </>
  );
}
