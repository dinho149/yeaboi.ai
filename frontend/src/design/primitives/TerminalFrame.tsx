/**
 * A window frame: traffic lights and a title bar, wrapped around content.
 *
 * yeaboi.ai frames its hero this way, and reusing it says something true — this
 * page came out of somebody's terminal:
 *
 * - **The share gate.** A teammate following a link has no context at all; the
 *   frame is what tells them where this came from.
 * - **A static export.** It is a document produced by a tool, and the header is
 *   the tool's mark.
 * - **The live boards.** They wear it too, now.
 *
 * That last one was a **no** for a long while, on the argument that the board
 * *is* the application rather than a picture of one, so terminal chrome would
 * be a costume — and that it would spend 40-odd vertical pixels a phone does
 * not have.
 *
 * The first half was simply wrong about who is looking. The host has the real
 * terminal; everyone else got a URL in a chat message and has exactly as little
 * context as the gate visitor two bullets up. A board reached over a Cloudflare
 * tunnel is no more "the application" to them than the gate was.
 *
 * The second half was a real cost, and it is answered rather than dismissed:
 * `PageShell` drops to a compact rendition below a large viewport, so the phone
 * pays for the title bar and not for the hero. See its density switch.
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
