/**
 * The app's own logic: route matching and the API client.
 *
 * Both are small enough to look correct and wrong in the ways that matter — a
 * parameter that swallows a slash routes a nested path to the wrong screen, and
 * a missing CSRF header turns every mutation into a 403 that looks like a
 * permissions bug.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api, readCookie } from './api';
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
