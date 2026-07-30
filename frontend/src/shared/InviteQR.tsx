/**
 * The invite panel: a QR code and a copyable join code.
 *
 * The QR stays an `<img>` pointing at `/api/qr`, deliberately. Generating it
 * client-side would mean bundling a QR library into every board page, and
 * `dangerouslySetInnerHTML` with a server-built SVG string would be the one
 * place in the whole codebase that parses server output as markup. An `<img>`
 * with a same-origin `src` needs neither.
 *
 * The src goes through {@link safeImageSrc} even though this app builds it
 * itself: the URL carries the board token, and a URL builder that could ever be
 * fed an attacker-influenced base is worth gating once, cheaply, at the point
 * of use.
 */

import { safeImageSrc } from '../runtime/url';
import { cx } from '../runtime/cx';
import styles from './shared.module.css';

export interface InviteQRProps {
  /** Same-origin QR endpoint, token already attached (see `runtime/api.apiUrl`). */
  qrSrc: string;
  /** The code teammates type on the gate, e.g. "K3P9-2QXA". */
  joinCode?: string;
  /** The URL to share. Shown as text so it can be read aloud or copied. */
  shareUrl?: string;
  className?: string | undefined;
}

export function InviteQR({ qrSrc, joinCode, shareUrl, className }: InviteQRProps) {
  const src = safeImageSrc(qrSrc);

  return (
    <div className={cx(styles['invite'], className)}>
      {src ? (
        <img
          className={styles['qr']}
          src={src}
          width={200}
          height={200}
          alt="QR code linking to this board"
        />
      ) : null}

      {joinCode ? (
        <p className={styles['inviteCode']}>
          <span className={styles['fieldLabel']}>Code</span>
          {/* A join code is read aloud and typed by hand, so it renders in the
              mono voice with wide tracking — 0/O and 1/I are the whole problem. */}
          <strong className={styles['codeValue']}>{joinCode}</strong>
        </p>
      ) : null}

      {shareUrl ? <p className={styles['inviteUrl']}>{shareUrl}</p> : null}
    </div>
  );
}
