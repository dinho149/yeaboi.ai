/**
 * The verdict a reader casts on a practice signal — the only request a *report*
 * sends, as opposed to the edits in `actions.ts`.
 *
 * Its own module rather than another action on that one: an edit rewrites the
 * document and answers with fresh state through the board runtime's `postJSON`,
 * while a vote is a bare same-origin POST that authenticates off the URL. They
 * share a server and nothing else, and folding them together would have meant
 * two different `mutate`s in one file, one shadowing the other.
 *
 * Every export bundle is inert by policy: `ARTIFACT_CSP` sets
 * `connect-src 'none'`, so a `fetch` from a written file or a finished snapshot
 * cannot leave the page at all. A *correctable* standup share is served under
 * `EDIT_CSP` instead, whose only difference is `connect-src 'self'`. So this
 * module is reachable in exactly one situation, and callers must gate on
 * `report.correctable` before offering a control that uses it.
 *
 * The request keys here are checked against the handler that reads them by
 * `tests/unit/test_web_request_keys.py`, which parses `mutate(` call sites with
 * literal paths and literal bodies — a key the server does not read fails
 * silently otherwise, which is the whole reason that guard exists.
 */

/** What the server did with a verdict. `applied: false` is ordinary, not an error. */
export interface VoteResult {
  ok: boolean;
  applied: boolean;
  reason: string;
}

/** `up` — the signal was right. `down` — it was wrong; hide it and remember. */
export type Verdict = 'up' | 'down';

/**
 * The token that got us past the gate, carried on the query string.
 *
 * Read from the URL rather than the payload: the payload is baked into the HTML
 * once, and the same document is served to every reader who joins, so a token
 * embedded in it would be the *first* reader's. It is already in `location`
 * because that is how the artifact GET authenticated.
 */
function token(): string {
  return new URLSearchParams(location.search).get('token') ?? '';
}

/**
 * POST one JSON body to a same-origin route, carrying the gate token.
 *
 * Named `mutate` and called with a literal body on purpose: that is the shape
 * `test_web_request_keys.py` parses to prove every key sent is a key the server
 * reads. Inline the `fetch` and the guard goes blind to this route.
 */
async function mutate(path: string, body: Record<string, unknown>): Promise<VoteResult> {
  const response = await fetch(`${path}?token=${encodeURIComponent(token())}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    return { ok: false, applied: false, reason: 'That could not be saved.' };
  }
  return (await response.json()) as VoteResult;
}

/**
 * Record a verdict on one member's practice signal.
 *
 * Resolves to `applied: false` when the signal is no longer in the run — someone
 * else answered it first, which on a page several people are reading at once is
 * an ordinary race, not a failure. Rejects only on transport failure: the host
 * closing the share, or the tunnel dropping.
 */
export async function votePractice(
  member: string,
  rule: string,
  verdict: Verdict,
  note: string,
): Promise<VoteResult> {
  return mutate('/api/practice-vote', { member, rule, verdict, note });
}
