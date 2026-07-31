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
  className?: string | undefined;
}

/**
 * The one-line invite: link then code, in the order they are used.
 *
 * Exported because the panel is not the only place this text is wanted — the
 * auto-copy on open sends exactly this, so both go through one function and the
 * clipboard cannot say something different from the screen.
 */
export function inviteText(shareUrl: string, joinCode: string): string {
  const lines = [];
  if (shareUrl) lines.push(shareUrl);
  if (joinCode) lines.push(`Access code: ${joinCode}`);
  return lines.join('\n');
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

      {/* Both fields render only once the values are in. They arrive from
          `GET /api/invite` rather than the boot payload, because the page is
          served unauthenticated and the code would be readable by any LAN peer
          without a token — see `retro/page.py`. */}
      {shareUrl ? <CopyField label="Link" value={shareUrl} /> : null}
      {joinCode ? <CopyField label="Code" value={joinCode} mono /> : null}
    </div>
  );
}
