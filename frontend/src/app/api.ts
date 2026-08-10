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

  let response: Response;
  try {
    response = await fetch(path, { ...init, method, headers, credentials: 'same-origin' });
  } catch {
    // A dead server and a dropped connection are indistinguishable here, and
    // both mean the same thing to a caller: nothing came back.
    return { ok: false, status: 0, error: 'could not reach the server' };
  }

  if (response.status === 204) return { ok: true, data: undefined as T };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, status: response.status, error: 'the server sent something unreadable' };
  }

  if (!response.ok) {
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
