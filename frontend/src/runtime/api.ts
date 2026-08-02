/**
 * The board HTTP client: credentials, request shaping, and the long-poll GET.
 *
 * ## Where credentials live and why
 *
 * Two secrets can arrive in the URL. The **token** grants read/write access to
 * the board; the **admin** secret rides only in the host's private link and
 * unlocks the host controls (timer, lock, theme/music cast, reveal).
 *
 * Both are lifted out of the URL on first load, stored in `sessionStorage`, and
 * then {@link stripCredentialsFromUrl} rewrites the address bar. That is not
 * cosmetic: a URL is copied into chat messages, lands in browser history, and
 * is read aloud in screen shares. The Python side already keeps it out of the
 * server log (`log_request` logs only `urlparse(self.path).path`), so this
 * closes the matching hole on the client.
 *
 * The token still travels as a query parameter on every request, because these
 * endpoints are also opened directly (`<img src="/api/qr?token=…">`) where no
 * header can be attached. Cookies are deliberately not used — they are shared
 * across tabs, which would break the per-tab access model in `storage.ts`.
 */

import { read, write } from './storage';

/** What one browser knows about itself and the board it is on. */
export interface Session {
  /** Board access token, or `''` when the visitor still has to enter a code. */
  token: string;
  /**
   * Host secret, or `''` for a guest.
   *
   * **Cosmetic only.** It decides what this browser *renders*; it decides
   * nothing about what the server allows. Every privileged endpoint re-checks
   * the secret with a constant-time compare (`_admin_authed`), so editing this
   * value in devtools reveals buttons that then fail server-side.
   */
  admin: string;
  /** Stable per-browser participant id. See `storage.participantId`. */
  pid: string;
}

/** Result of a JSON request. `ok:false` carries the status for the caller to map. */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number };

/** Outcome of one long-poll. `changed:false` means the server answered 304. */
export type PollResult<T> =
  | { changed: true; data: T; etag: string }
  | { changed: false; etag: string }
  | { changed: false; etag: string; error: true };

/**
 * Collect the token and admin secret from the URL or session storage.
 *
 * The URL wins when both are present — following a fresh link is how you switch
 * boards, and the stale session value would otherwise pin you to the old one.
 */
export function loadSession(prefix: string, pid: string): Session {
  const params = new URLSearchParams(location.search);
  const token = params.get('token') ?? read('session', `${prefix}_token`) ?? '';
  const admin = params.get('admin') ?? read('session', `${prefix}_admin`) ?? '';
  if (token) write('session', `${prefix}_token`, token);
  if (admin) write('session', `${prefix}_admin`, admin);
  return { token, admin, pid };
}

/**
 * Remove `token`, `admin` and the invite `code` from the address bar.
 *
 * `replaceState`, not `pushState`: the credential-bearing URL must not stay in
 * the back stack. Wrapped because `history` is unavailable in some embedded
 * webviews and throws over `file://` in a few browsers — a failure here is
 * cosmetic and must never stop the board from booting.
 *
 * The `code` cases are load-bearing rather than tidiness. `JoinGate` calls this
 * *before* it auto-submits a code lifted from the link, so a reload after a
 * rejection carries nothing and makes no request — which is what keeps a stale
 * link from walking an IP into `JoinLimiter`'s eight-failure lockout. And a
 * visitor who already has a token never renders the gate at all, so without the
 * fragment branch here their `#code=` would sit in the address bar, and in every
 * screenshot, for the whole session.
 *
 * The fragment keeps whatever else it was carrying (an anchor, a future param):
 * only the `code` segment is dropped, and by filtering the raw `&`-separated
 * parts rather than round-tripping through `URLSearchParams`, which would
 * re-encode the survivors and hand a bare `#…&section` back as `#section=`.
 */
export function stripCredentialsFromUrl(): void {
  try {
    const url = new URL(location.href);
    const parts = url.hash.replace(/^#/, '').split('&');
    const codedHash = parts.some(isCode);
    const dirty =
      url.searchParams.has('token') ||
      url.searchParams.has('admin') ||
      url.searchParams.has('code') ||
      codedHash;
    if (!dirty) return;
    url.searchParams.delete('token');
    url.searchParams.delete('admin');
    url.searchParams.delete('code');
    let fragment = url.hash;
    if (codedHash) {
      const rest = parts.filter((part) => !isCode(part)).join('&');
      fragment = rest ? `#${rest}` : '';
    }
    history.replaceState(null, '', `${url.pathname}${url.search}${fragment}`);
  } catch {
    /* no history API — the URL keeps its query, everything else still works */
  }
}

/** True for the one fragment segment this strips: `code=…`, or a bare `code`. */
function isCode(part: string): boolean {
  return part === 'code' || part.startsWith('code=');
}

/** Build a same-origin API URL carrying the token and any extra parameters. */
export function apiUrl(session: Session, path: string, extra: Record<string, string> = {}): string {
  const [base, existing = ''] = path.split('?');
  const params = new URLSearchParams(existing);
  if (session.token) params.set('token', session.token);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  const query = params.toString();
  return query ? `${base}?${query}` : (base ?? path);
}

/**
 * POST JSON to a board endpoint.
 *
 * `pid` and `admin` are merged into every body. Sending `admin` unconditionally
 * is intentional and harmless: only `/api/admin/*` and `/api/timer` look at it,
 * and for a guest it is the empty string, which fails the compare.
 */
export async function postJSON<T>(
  session: Session,
  path: string,
  body: Record<string, unknown> = {},
  init: RequestInit = {}
): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(apiUrl(session, path), {
      ...init,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
      body: JSON.stringify({ pid: session.pid, admin: session.admin, ...body }),
    });
  } catch {
    // Network-level failure (offline, tunnel dropped, request aborted). Status 0
    // so callers can tell "could not reach the board" from "the board said no".
    return { ok: false, status: 0 };
  }
  if (!response.ok) return { ok: false, status: response.status };
  // 204 and an empty 200 are both legitimate — POST /api/presence?quiet=1
  // answers `{"ok":true}` precisely so it does not have to ship 40 KB of state.
  const text = await response.text();
  return { ok: true, data: (text ? JSON.parse(text) : {}) as T };
}

/**
 * One long-poll of `/api/state`.
 *
 * The ETag **is** the cursor. Sending the one we hold tells the server both
 * "here is where I am" and "hold this request until I am behind"; there is no
 * separate `since=<revision>` parameter that could drift out of step with it.
 * A client that is already behind is answered immediately rather than parked,
 * so a reconnecting peer never waits for a change it has already missed.
 *
 * `waitSeconds = 0` degrades this to an ordinary conditional GET, which is what
 * the first request of a session does and what a client falls back to when the
 * server's hold slots are full.
 */
export async function pollState<T>(
  session: Session,
  {
    etag = '',
    waitSeconds = 0,
    signal,
    path = '/api/state',
  }: { etag?: string; waitSeconds?: number; signal?: AbortSignal; path?: string } = {}
): Promise<PollResult<T>> {
  const extra: Record<string, string> = { pid: session.pid };
  if (waitSeconds > 0) extra['wait'] = String(waitSeconds);

  let response: Response;
  try {
    response = await fetch(apiUrl(session, path, extra), {
      // no-store on the request as well as the response: a held request that a
      // proxy decided to cache would pin the board to one snapshot forever.
      cache: 'no-store',
      headers: etag ? { 'If-None-Match': etag } : {},
      ...(signal ? { signal } : {}),
    });
  } catch {
    return { changed: false, etag, error: true };
  }

  if (response.status === 304) return { changed: false, etag };
  if (!response.ok) return { changed: false, etag, error: true };

  const next = response.headers.get('ETag') ?? '';
  const data = (await response.json()) as T;
  // Keep the previous tag if the server sent none — an empty tag would make the
  // next request unconditional, and the loop would spin at full speed.
  return { changed: true, data, etag: next || etag };
}
