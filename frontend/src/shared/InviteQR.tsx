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
import { CopyField } from './CopyField';
import styles from './shared.module.css';

export interface InviteQRProps {
  /** Same-origin QR endpoint, token already attached (see `runtime/api.apiUrl`). */
  qrSrc: string;
  /**
   * The code teammates type on the gate, e.g. "K3P9-2QXA".
   *
   * `undefined` is the normal state for the first frame — it arrives from
   * `GET /api/invite` after the panel opens, not from the boot payload.
   */
  joinCode?: string | undefined;
  /** The URL to share. Shown as text so it can be read aloud as well as copied. */
  shareUrl?: string | undefined;
  /**
   * The whole invite as one URL — `shareUrl` with the code in its fragment.
   *
   * Built by `sharing.access.invite_url` and never re-derived here; this
   * component only renders what arrived.
   */
  inviteUrl?: string | undefined;
  className?: string | undefined;
}

export function InviteQR({ qrSrc, joinCode, shareUrl, inviteUrl, className }: InviteQRProps) {
  const src = safeImageSrc(qrSrc);

  return (
    <div className={cx(styles['invite'], className)}>
      {src ? (
        <img
          className={styles['qr']}
          src={src}
          width={280}
          height={280}
          alt="QR code linking to this board"
        />
      ) : null}

      {/* Every field renders only once the values are in. They arrive from
          `GET /api/invite` rather than the boot payload, because the page is
          served unauthenticated and the code would be readable by anyone who
          reaches the board — see `retro/page.py`.

          Invite leads: it is one URL that carries the code, so it is the only
          one of the three that is a complete thing to send someone. It has a
          button of its own rather than relying on the auto-copy, because
          `copyText` legitimately fails (insecure context, expired activation
          window) and the panel must still have a way out.

          Link and Code stay below it, separately copyable, for the host who
          wants to post the address in a channel and pass the code another way. */}
      {inviteUrl ? <CopyField label="Invite link" value={inviteUrl} /> : null}
      {joinCode ? <CopyField label="Code" value={joinCode} mono /> : null}
    </div>
  );
}
