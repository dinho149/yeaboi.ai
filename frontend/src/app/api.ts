/**
 * The one place the app talks to its server.
 *
 * Every export surface before this was inert by policy — `ARTIFACT_CSP` sets
 * `connect-src 'none'`, so a written file physically cannot make a request. The
 * app is the first bundle that is *allowed* to, which means it is also the first
 * that has to get the details right, and it gets them right in one module rather
 * than at each call site.
 *
 * Two of those details are not obvious:
 *
 * 1. **CSRF.** The server sets a readable `yeaboi_csrf` cookie and requires it
 *    echoed in a header on every unsafe method. That is a double submit; the
 *    cookie is readable *on purpose* (see `app/sessions.py`). Doing it here
 *    means a new mutation cannot forget it.
 * 2. **401 is a state, not an error.** A signed-out user hitting a protected
 *    route is the normal cold-start path, so it resolves to a typed result
 *    instead of throwing — a thrown 401 ends up in an error boundary that says
 *    "something went wrong" when the truth is "please sign in".
 */

const CSRF_COOKIE = 'yeaboi_csrf';
const CSRF_HEADER = 'X-Yeaboi-CSRF';

/**
 * How long to wait before giving up on a request.
 *
 * Without a deadline a request that never settles leaves the screen on
 * `loading` for as long as the tab is open. Browsers do eventually time a
 * socket out, but "eventually" is minutes and is not something to rely on.
 */
const TIMEOUT_MS = 20_000;

/**
 * What to do when the server says the session is gone.
 *
 * A session ends for reasons that have nothing to do with the current click:
 * it expired, or someone used "sign out on every device" from another machine.
 * Without a central answer, every screen renders "unauthorized" as an error and
 * keeps doing so until the tab is reloaded — the app looks broken when what
 * actually happened is that it signed you out.
 *
 * A registered callback rather than a thrown error so the decision lives in one
 * place (App's session state) and not at each call site.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/**
 * Requests where a 401 is an answer rather than an expiry.
 *
 * Signing in *is* the request that has no session yet, and asking who you are
 * before you are anyone is a normal cold start. Routing those through the
 * handler would sign you out of the sign-in screen.
 */
const AUTH_PATHS = ['/api/auth/session', '/api/auth/request', '/api/auth/claim', '/api/auth/me'];

/** Read one cookie. Returns '' when absent — never throws on a malformed jar. */
export function readCookie(name: string): string {
  const target = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(target)) return decodeURIComponent(trimmed.slice(target.length));
  }
  return '';
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; error: string };

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/**
 * Call the API.
 *
 * `path` is a literal like '/api/projects' rather than a built URL, and the body
 * is a plain object — `test_web_request_keys.py` parses these call sites and
 * requires every key sent to be one a handler actually reads. That guard exists
 * because the opposite direction fails *silently*: `payload.get(key, default)`
 * just returns the default when the client renames a field.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<ApiResult<T>> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set('Content-Type', 'application/json');
  if (UNSAFE.has(method)) headers.set(CSRF_HEADER, readCookie(CSRF_COOKIE));

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: 'same-origin',
      signal: controller.signal,
    });
  } catch (cause) {
    // A dead server, a dropped connection and a timeout are indistinguishable
    // to a caller in every way except what to say about them.
    const timedOut = cause instanceof DOMException && cause.name === 'AbortError';
    return {
      ok: false,
      status: 0,
      error: timedOut ? 'the server took too long to answer' : 'could not reach the server',
    };
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 204) return { ok: true, data: undefined as T };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, status: response.status, error: 'the server sent something unreadable' };
  }

  if (!response.ok) {
    if (response.status === 401 && !AUTH_PATHS.includes(path)) {
      onUnauthorized?.();
    }
    const error =
      typeof payload === 'object' && payload !== null && 'error' in payload
        ? String((payload as { error: unknown }).error)
        : `request failed (${response.status})`;
    return { ok: false, status: response.status, error };
  }
  return { ok: true, data: payload as T };
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, body === undefined ? { method: 'POST' } : { method: 'POST', body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' });
