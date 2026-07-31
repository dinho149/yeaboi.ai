/**
 * The payload contract for the share gate.
 *
 * The gate was the one surface with no boot island at all — deliberately, back
 * when it was required to say nothing whatever about what was behind it. It
 * says one thing now: which mode. See `src/yeaboi/sharing/gate.py` for the full
 * list of what is still withheld, which is everything else.
 *
 * Every value here comes from the fixed vocabulary in `web/brand.py`. Nothing
 * in it varies with the share, which is why the document is still cached
 * per-mode on the server.
 */

import { readBoot } from '../runtime/boot';

export interface GateBoot {
  /**
   * The `[data-mode]` accent, already resolved by `accent_mode()`. Empty for an
   * unbranded gate — `performance`, or a mode the vocabulary does not know.
   */
  mode: string;
  /**
   * The word for the display face. Sent rather than derived from `mode`,
   * because they are not the same thing: `reporting` sets as `report`, and
   * `roadmap` wears planning's accent while keeping its own word.
   */
  wordmark: string;
  frameTitle: string;
  heading: string;
  eyebrow: string;
  cta: string;
}

/**
 * Read the gate's payload, tolerating its absence.
 *
 * `readBoot`, not `requireBoot`: a gate with no island must still boot. That is
 * not hypothetical — `frontend/dev/gate.html` has none, and a browser holding a
 * cached copy of an older document would have none either.
 */
export function readGateBoot(): GateBoot | null {
  return readBoot<GateBoot>();
}
