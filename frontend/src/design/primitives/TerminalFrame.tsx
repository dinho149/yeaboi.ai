/**
 * A window frame: traffic lights and a title bar, wrapped around content.
 *
 * yeaboi.ai frames its hero this way, and reusing it says something true — this
 * page came out of somebody's terminal. That claim is only true in some places,
 * so the frame is deliberately **not** available everywhere:
 *
 * - **Yes** on the share gate. A teammate following a link has no context at
 *   all; the frame is what tells them where this came from.
 * - **Yes** on a static export. It is a document produced by a tool, and the
 *   header is the tool's mark.
 * - **No** on the live boards. The board *is* the application, not a picture of
 *   one, and dressing it as a terminal window would be a costume — it would
 *   also waste 40-odd vertical pixels on a phone, which the boards do not have.
 *
 * The lights are inert: three dots, not controls. They are `aria-hidden`, they
 * are not buttons, and nothing happens when you click them. Making them look
 * interactive would be a lie about what the page can do.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface TerminalFrameProps {
  /**
   * Title-bar text. Conventionally `yeaboi — <mode>`, matching what the host's
   * own terminal tab says.
   */
  title: string;
  children: ReactNode;
  className?: string | undefined;
}

export function TerminalFrame({ title, children, className }: TerminalFrameProps) {
  return (
    <div className={cx(styles['frame'], className)}>
      <div className={styles['frameBar']}>
        <span className={styles['frameLights']} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        {/* Not a heading: the page's real h1 is inside. This is chrome, and a
            screen reader walking the heading outline should not trip over it. */}
        <span className={styles['frameTitle']}>{title}</span>
      </div>
      <div className={styles['frameBody']}>{children}</div>
    </div>
  );
}
