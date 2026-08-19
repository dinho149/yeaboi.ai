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

import { useState } from 'react';

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
  /**
   * The whole invite as one URL — the board's address with the code in its
   * fragment.
   *
   * Built by `sharing.access.invite_url` and never re-derived here; this
   * component only renders what arrived.
   */
  inviteUrl?: string | undefined;
  /**
   * The board's bare address, without the code in its fragment.
   *
   * Only the ship board sends it: retro and poker deliberately offer the one
   * link that carries the code, because a second URL beside it is the same
   * invite said twice.
   */
  shareUrl?: string | undefined;
  /**
   * Why there is no link yet. Boards that do not report it get `pending`, which
   * is what an empty url used to mean unconditionally.
   */
  shareState?: 'ready' | 'pending' | 'failed' | 'off' | undefined;
  className?: string | undefined;
}

/** What to say while there is nothing to send. */
const WAITING: Record<'pending' | 'failed' | 'off', string> = {
  pending: 'Setting up the shared link — this takes a moment.',
  failed: 'The shared link could not be set up. Retry it from the terminal running this board.',
  off: 'Sharing is off for this board, so there is no link to send.',
};

export function InviteQR({ qrSrc, joinCode, inviteUrl, shareUrl, shareState, className }: InviteQRProps) {
  const src = safeImageSrc(qrSrc);
  // The endpoint answers 503 until the tunnel is up, and a broken image with
  // its alt text showing is the worst of the three things it could do.
  const [unavailable, setUnavailable] = useState(false);
  // The QR having failed is the one signal from in here that the board is not
  // shared. Not `!inviteUrl`, which is also true for the first frame after the
  // panel opens, while both are still in flight.
  const waiting = (unavailable || !src) && shareState !== 'ready';

  return (
    <div className={cx(styles['invite'], className)}>
      {src && !unavailable ? (
        <img
          className={styles['qr']}
          src={src}
          width={280}
          height={280}
          alt="QR code linking to this board"
          onError={() => setUnavailable(true)}
        />
      ) : null}

      {waiting ? <p className={styles['panelNote']}>{WAITING[shareState ?? 'pending']}</p> : null}

      {/* Every field renders only once the values are in. They arrive from
          `GET /api/invite` rather than the boot payload, because the page is
          served unauthenticated and the code would be readable by anyone who
          reaches the board — see `retro/page.py`.

          Invite leads: it is one URL that carries the code, so it is the only
          one of the three that is a complete thing to send someone. It has a
          button of its own rather than relying on the auto-copy, because
          `copyText` legitimately fails (insecure context, expired activation
          window) and the panel must still have a way out.

          The code stays below it, separately copyable, for the host who wants
          to read it out while the link goes in a channel. */}
      {inviteUrl ? <CopyField label="Invite link" value={inviteUrl} /> : null}
      {shareUrl ? <CopyField label="Link" value={shareUrl} /> : null}
      {joinCode ? <CopyField label="Code" value={joinCode} mono /> : null}
    </div>
  );
}
