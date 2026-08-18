/**
 * The ship board's boot payload.
 *
 * Tiny and static, like every board's island. Everything that moves during a
 * run — the status, the phase checklist, the agent's activity, the diff, the
 * verdict — arrives over `/api/state` and is scrubbed on the way out
 * (`ship/board.py`). The island carries only the chrome and the two identifying
 * strings, and `GET /` is unauthenticated, so nothing secret goes here.
 */
import { requireBoot } from '../runtime/boot';
import type { PageChrome } from '../shared/chrome';

export interface ShipBoot {
  chrome: PageChrome;
  /** The story being shipped, shown beside the title. May be empty. */
  story: string;
  /** The project the run is in. May be empty. */
  project: string;
}

export function readShipBoot(): ShipBoot {
  return requireBoot<ShipBoot>();
}
