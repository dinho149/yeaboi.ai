/**
 * Fetch the invite when the panel opens, and put it on the clipboard.
 *
 * ## Why it is fetched at all
 *
 * The join code is not in the boot payload and must not be. `GET /` is
 * unauthenticated, so everything in the JSON island is readable by anyone who
 * reaches the board — `retro/page.py` says so directly above `board_config`. A
 * code shipped there would be the gate handing out its own key. So it comes from
 * `GET /api/invite`, which is token-gated.
 *
 * ## Why it copies without being asked
 *
 * Opening the invite panel has one purpose. Every path out of it ends with the
 * link somewhere else — a chat window, a calendar invite — so doing that step
 * on the way in removes the most common interaction entirely.
 *
 * It works because opening the panel is a click: the write happens inside the
 * activation window that click opened. If a browser refuses anyway the panel is
 * unharmed — the values are on screen with their own copy buttons, and the toast
 * simply does not claim something untrue.
 *
 * ## Why it refetches on every open
 *
 * The board's Cloudflare tunnel comes up a few seconds after the board itself,
 * and until it does the server has no address worth handing out — it binds
 * loopback. A panel opened in that window would cache an answer that is about to
 * be replaced by the real one, so it asks again every time instead.
 */

import { useCallback, useEffect, useState } from 'react';

import { apiUrl, type Session } from '../runtime/api';
import { copyText } from '../runtime/clipboard';
import { inviteText } from '../shared/InviteQR';
import type { InviteInfo } from '../types/board';

export interface UseInvite {
  /** The link and code, or `null` until the fetch lands. */
  invite: InviteInfo | null;
  /** Toast text for the auto-copy, or `null`. */
  notice: string | null;
  /** Clear the toast. Pass straight to `<Toast onDismiss>`. */
  dismiss: () => void;
}

export function useInvite(session: Session, open: boolean): UseInvite {
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const dismiss = useCallback(() => setNotice(null), []);

  useEffect(() => {
    if (!open) return;

    // Guards a fetch that resolves after the panel has been closed again, which
    // would otherwise raise a toast over a board with no panel on it.
    let live = true;

    void (async () => {
      try {
        const response = await fetch(apiUrl(session, '/api/invite'));
        if (!response.ok) return;
        const data = (await response.json()) as InviteInfo;
        if (!live) return;

        setInvite(data);

        // No link yet means the tunnel is still coming up, and the server
        // deliberately sent an empty `shareUrl` rather than the loopback address
        // the host is sitting on. Copying the code on its own would be a
        // half-invite — the reader gets a password and no door — so the panel
        // shows what it has and says nothing about the clipboard.
        if (!data.shareUrl) return;

        const copied = await copyText(inviteText(data.shareUrl, data.joinCode));
        if (!live) return;
        // Only claimed when it actually happened. "Copied" over an empty
        // clipboard is worse than saying nothing, because it stops the reader
        // reaching for the buttons that would have worked.
        setNotice(copied ? 'Invite copied to your clipboard' : null);
      } catch {
        // The host closed the board, or the tunnel dropped. The panel still
        // shows the QR, and the board's own reconnect notice covers the rest.
      }
    })();

    return () => {
      live = false;
    };
  }, [open, session]);

  return { invite, notice, dismiss };
}
