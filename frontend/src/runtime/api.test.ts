/**
 * The HTTP client: credential handling and the ETag long-poll.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchBody, fetchInit, fetchUrl } from '../test/calls';
import { apiUrl, loadSession, pollState, postJSON, stripCredentialsFromUrl, type Session } from './api';

const SESSION: Session = { token: 'tok', admin: '', pid: 'pid-1' };

function jsonResponse(body: unknown, init: ResponseInit & { etag?: string } = {}): Response {
  const headers = new Headers(init.headers);
  if (init.etag) headers.set('ETag', init.etag);
  return new Response(JSON.stringify(body), { ...init, headers });
}

beforeEach(() => {
  sessionStorage.clear();
  history.replaceState(null, '', '/');
});

afterEach(() => vi.restoreAllMocks());

describe('apiUrl', () => {
  it('attaches the token', () => {
    expect(apiUrl(SESSION, '/api/state')).toBe('/api/state?token=tok');
  });

  it('preserves an existing query', () => {
    expect(apiUrl(SESSION, '/api/presence?quiet=1')).toBe('/api/presence?quiet=1&token=tok');
  });

  it('merges extra parameters', () => {
    expect(apiUrl(SESSION, '/api/state', { pid: 'p', wait: '25' })).toBe('/api/state?token=tok&pid=p&wait=25');
  });

  it('omits the token when there is none', () => {
    expect(apiUrl({ ...SESSION, token: '' }, '/api/state')).toBe('/api/state');
  });
});

describe('loadSession', () => {
  it('lifts credentials out of the URL and remembers them per tab', () => {
    history.replaceState(null, '', '/?token=abc&admin=xyz');
    const session = loadSession('retro', 'pid-1');

    expect(session).toEqual({ token: 'abc', admin: 'xyz', pid: 'pid-1' });
    expect(sessionStorage.getItem('retro_token')).toBe('abc');
  });

  it('recovers the token from storage on a later navigation', () => {
    sessionStorage.setItem('retro_token', 'stored');
    expect(loadSession('retro', 'p').token).toBe('stored');
  });

  it('lets a fresh link win over a stored token', () => {
    // Following a new link is how you switch boards; a stale session value
    // would otherwise pin the tab to the old one.
    sessionStorage.setItem('retro_token', 'stored');
    history.replaceState(null, '', '/?token=fresh');
    expect(loadSession('retro', 'p').token).toBe('fresh');
  });
});

describe('stripCredentialsFromUrl', () => {
  it('removes the token and admin secret from the address bar', () => {
    // A board URL gets pasted into chat and read aloud in screen shares. The
    // server already keeps it out of its log; this is the other half.
    history.replaceState(null, '', '/?token=abc&admin=xyz&keep=1');
    stripCredentialsFromUrl();

    expect(location.search).toBe('?keep=1');
  });

  it('does nothing when there is nothing to strip', () => {
    history.replaceState(null, '', '/board?x=1');
    stripCredentialsFromUrl();
    expect(location.search).toBe('?x=1');
  });
});

describe('postJSON', () => {
  it('merges pid and admin into every body', () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    void postJSON({ ...SESSION, admin: 'secret' }, '/api/card', { text: 'hi' });

    const body = fetchBody(fetchMock);
    expect(body).toEqual({ pid: 'pid-1', admin: 'secret', text: 'hi' });
  });

  it('reports the status for a rejection rather than throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 403 })));
    expect(await postJSON(SESSION, '/api/card')).toEqual({ ok: false, status: 403 });
  });

  it('reports status 0 when the network is gone', async () => {
    // Distinguishable from "the board said no", which is what the UI needs to
    // tell "you are offline" from "that was not allowed".
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed to fetch')));
    expect(await postJSON(SESSION, '/api/card')).toEqual({ ok: false, status: 0 });
  });

  it('tolerates an empty success body', async () => {
    // POST /api/presence?quiet=1 exists precisely so the response does not have
    // to carry 40 KB of state.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 200 })));
    expect(await postJSON(SESSION, '/api/presence?quiet=1')).toEqual({ ok: true, data: {} });
  });
});

describe('pollState', () => {
  it('sends no If-None-Match on the first request and does not ask to wait', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ revision: 1 }, { etag: 'W/"a"' }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await pollState(SESSION, {});

    expect(fetchUrl(fetchMock)).toBe('/api/state?token=tok&pid=pid-1');
    expect(fetchInit(fetchMock).headers).toEqual({});
    expect(result).toEqual({ changed: true, data: { revision: 1 }, etag: 'W/"a"' });
  });

  it('sends the ETag as the cursor and asks the server to hold', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 304 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await pollState(SESSION, { etag: 'W/"a"', waitSeconds: 25 });

    expect(fetchUrl(fetchMock)).toContain('wait=25');
    expect(fetchInit(fetchMock).headers).toEqual({ 'If-None-Match': 'W/"a"' });
    // 304 means "you are current" — no body, and the cursor is unchanged.
    expect(result).toEqual({ changed: false, etag: 'W/"a"' });
  });

  it('keeps the old cursor when the server sends no ETag', async () => {
    // An empty tag would make the next request unconditional and the loop would
    // spin at full speed against the server.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ revision: 2 })));
    const result = await pollState(SESSION, { etag: 'W/"a"', waitSeconds: 25 });
    expect(result).toEqual({ changed: true, data: { revision: 2 }, etag: 'W/"a"' });
  });

  it('flags an error without losing the cursor', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    expect(await pollState(SESSION, { etag: 'W/"a"' })).toEqual({ changed: false, etag: 'W/"a"', error: true });
  });

  it('treats a 5xx as an error, not as "no change"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 503 })));
    expect(await pollState(SESSION, { etag: 'W/"a"' })).toEqual({ changed: false, etag: 'W/"a"', error: true });
  });
});
