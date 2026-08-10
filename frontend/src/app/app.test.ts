/**
 * The app's own logic: route matching and the API client.
 *
 * Both are small enough to look correct and wrong in the ways that matter — a
 * parameter that swallows a slash routes a nested path to the wrong screen, and
 * a missing CSRF header turns every mutation into a 403 that looks like a
 * permissions bug.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, readCookie, setUnauthorizedHandler } from './api';
import { Routes } from './router';

describe('Routes', () => {
  const routes = new Routes(['/', '/projects', '/projects/{id}', '/projects/{id}/artifacts/{artifactId}']);

  it('matches a bare path', () => {
    expect(routes.match('/projects')?.pattern).toBe('/projects');
  });

  it('captures one parameter', () => {
    expect(routes.match('/projects/prj_1')?.params).toEqual({ id: 'prj_1' });
  });

  it('captures two', () => {
    expect(routes.match('/projects/prj_1/artifacts/art_2')?.params).toEqual({
      id: 'prj_1',
      artifactId: 'art_2',
    });
  });

  it('does not let a parameter swallow a slash', () => {
    // '/projects/a/b' matching '/projects/{id}' with id='a/b' would render the
    // project screen for a URL that means something else entirely.
    expect(new Routes(['/projects/{id}']).match('/projects/a/b')).toBeNull();
  });

  it('ignores a trailing slash', () => {
    expect(routes.match('/projects/')?.pattern).toBe('/projects');
  });

  it('returns null for an unknown path', () => {
    expect(routes.match('/nope')).toBeNull();
  });

  it('decodes an encoded segment', () => {
    expect(new Routes(['/p/{name}']).match('/p/a%20b')?.params['name']).toBe('a b');
  });
});

describe('readCookie', () => {
  it('reads one value out of a jar', () => {
    Object.defineProperty(document, 'cookie', { value: 'a=1; yeaboi_csrf=tok; b=2', configurable: true });
    expect(readCookie('yeaboi_csrf')).toBe('tok');
  });

  it('returns empty for an absent cookie', () => {
    Object.defineProperty(document, 'cookie', { value: 'a=1', configurable: true });
    expect(readCookie('nope')).toBe('');
  });
});

describe('api', () => {
  beforeEach(() => {
    Object.defineProperty(document, 'cookie', { value: 'yeaboi_csrf=tok', configurable: true });
  });

  it('sends the CSRF header on an unsafe method', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await api('/api/projects', { method: 'POST', body: '{}' });
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get('X-Yeaboi-CSRF')).toBe('tok');
  });

  it('does not send it on a read', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await api('/api/projects');
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get('X-Yeaboi-CSRF')).toBeNull();
  });

  it('turns a 401 into a result rather than throwing', async () => {
    // A signed-out cold start is the normal path, not an error boundary.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'unauthorized' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const result = await api('/api/auth/me');
    expect(result).toEqual({ ok: false, status: 401, error: 'unauthorized' });
  });

  it('reports an unreachable server instead of throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    const result = await api('/api/projects');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.status).toBe(0);
  });

  it('survives a body that is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>502</html>', { status: 502 })));
    const result = await api('/api/projects');
    expect(result.ok).toBe(false);
  });
});


describe('session expiry', () => {
  afterEach(() => setUnauthorizedHandler(null));

  function jsonResponse(body: unknown, status: number) {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  it('reports a 401 on an ordinary request so the app can sign out', async () => {
    // A session ends for reasons unrelated to this click - it expired, or
    // another device signed everything out. Without this every screen renders
    // "unauthorized" as an error until the tab is reloaded.
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: 'unauthorized' }, 401)));
    await api('/api/projects');
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('does not fire for the sign-in request itself', async () => {
    // Signing in IS the request with no session yet; routing it through the
    // handler would sign you out of the sign-in screen.
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: 'nope' }, 401)));
    await api('/api/auth/session', { method: 'POST', body: '{}' });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('does not fire for the cold-start identity check', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: 'unauthorized' }, 401)));
    await api('/api/auth/me');
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('does not fire on other failures', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: 'nope' }, 403)));
    await api('/api/projects');
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

describe('slow connections', () => {
  it('gives up rather than loading forever, and says which it was', async () => {
    vi.useFakeTimers();
    try {
      // A request that never settles otherwise holds the screen on `loading`
      // for as long as the tab is open.
      vi.stubGlobal(
        'fetch',
        vi.fn(
          (_input: unknown, init: RequestInit) =>
            new Promise((_resolve, reject) => {
              init.signal?.addEventListener('abort', () =>
                reject(new DOMException('aborted', 'AbortError')),
              );
            }),
        ),
      );
      const pending = api('/api/projects');
      await vi.advanceTimersByTimeAsync(25_000);
      const result = await pending;
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.error).toContain('too long');
    } finally {
      vi.useRealTimers();
    }
  });
});
