/**
 * The long-poll loop.
 *
 * The teardown case is the one worth having: a parked request pins one of the
 * server's four per-IP hold slots for up to 25 s, so a loop that is not aborted
 * on unmount keeps a slot reserved for a tab nobody is looking at.
 */

import { render, waitFor } from '@testing-library/preact';
import { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createBoardStore } from '../store/boardStore';
import type { Session } from '../runtime/api';
import { fetchInit, fetchUrl } from '../test/calls';
import { useBoardStream } from './useBoardStream';

const SESSION: Session = { token: 'tok', admin: '', pid: 'pid-1' };

interface Snap {
  revision: number;
}

function snapshot(revision: number, etag: string): Response {
  return new Response(JSON.stringify({ revision }), { headers: { ETag: etag } });
}

/**
 * A fetch that answers each call from a script, then parks forever.
 *
 * Parking rather than 404ing after the script runs out is what makes this a
 * faithful stand-in: the real endpoint holds the request open, and a mock that
 * returned immediately would turn the loop into a hot spin in every test.
 *
 * `maxConcurrent` records how many requests were in flight at once, which is
 * the only direct way to observe "one loop" versus "two overlapping loops".
 */
function scriptedFetch(responses: Response[]) {
  let call = 0;
  let inFlight = 0;
  const mock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
    inFlight += 1;
    mock.maxConcurrent = Math.max(mock.maxConcurrent, inFlight);
    const response = responses[call];
    call += 1;
    if (response) {
      inFlight -= 1;
      return response;
    }
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        inFlight -= 1;
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }) as ReturnType<typeof vi.fn> & { maxConcurrent: number };
  mock.maxConcurrent = 0;
  return mock;
}

function Probe({ store, enabled = true }: { store: ReturnType<typeof createBoardStore<Snap>>; enabled?: boolean }) {
  const status = useBoardStream({ session: SESSION, store, enabled });
  return <span data-testid="status">{status}</span>;
}

afterEach(() => vi.restoreAllMocks());

describe('useBoardStream', () => {
  it('applies the first snapshot and reports live', async () => {
    vi.stubGlobal('fetch', scriptedFetch([snapshot(1, 'W/"a"')]));
    const store = createBoardStore<Snap>();
    const { getByTestId } = render(<Probe store={store} />);

    await waitFor(() => expect(store.getSnapshot()).toEqual({ revision: 1 }));
    expect(getByTestId('status').textContent).toBe('live');
  });

  it('re-issues immediately, carrying the ETag it now holds', async () => {
    const fetchMock = scriptedFetch([snapshot(1, 'W/"a"'), snapshot(2, 'W/"b"')]);
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<Snap>();
    render(<Probe store={store} />);

    await waitFor(() => expect(store.getSnapshot()).toEqual({ revision: 2 }));
    // First request: no cursor, and no ?wait= — asking the server to hold when
    // we hold no ETag would burn a slot for a request it answers instantly.
    expect(fetchUrl(fetchMock, 0)).not.toContain('wait=');
    expect(fetchUrl(fetchMock, 1)).toContain('wait=25');
    expect(fetchInit(fetchMock, 1).headers).toEqual({ 'If-None-Match': 'W/"a"' });
  });

  it('does not start without a token', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<Snap>();
    render(<Probe store={{ ...store }} enabled={false} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reports retrying and backs off after a failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    const store = createBoardStore<Snap>();
    const { getByTestId } = render(<Probe store={store} />);

    // 'retrying' is worth surfacing: over a tunnel on a phone, "the board is
    // quiet" and "you fell off the network" look identical otherwise.
    await waitFor(() => expect(getByTestId('status').textContent).toBe('retrying'));
  });

  it('aborts the parked request on teardown instead of leaving a slot held', async () => {
    // A held request pins one of the server's four per-IP slots for up to 25 s.
    // Unmounting without aborting means a tab that navigated away keeps a slot
    // for the next teammate who tries to connect.
    const fetchMock = scriptedFetch([snapshot(1, 'W/"a"')]);
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<Snap>();
    const { unmount } = render(<Probe store={store} />);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
    const parked = fetchInit(fetchMock, fetchMock.mock.calls.length - 1).signal as AbortSignal;
    expect(parked.aborted).toBe(false);

    unmount();
    expect(parked.aborted).toBe(true);
  });

  it('runs exactly one loop under StrictMode', async () => {
    // Included because the plan flagged React's development double-invoke as a
    // hazard here. It is not one *while we run preact*: preact/compat exports
    // StrictMode as Fragment. This pins that assumption, so flipping the alias
    // back to React turns a silent behaviour change into a failing test.
    const fetchMock = scriptedFetch([snapshot(1, 'W/"a"'), snapshot(2, 'W/"b"')]);
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<Snap>();

    render(
      <StrictMode>
        <Probe store={store} />
      </StrictMode>
    );
    await waitFor(() => expect(store.getSnapshot()).toEqual({ revision: 2 }));

    // One request in flight at any moment. Two overlapping loops would show 2.
    expect(fetchMock.maxConcurrent).toBe(1);
  });

  it('stops polling when unmounted', async () => {
    const fetchMock = scriptedFetch([snapshot(1, 'W/"a"')]);
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<Snap>();
    const { unmount } = render(<Probe store={store} />);

    await waitFor(() => expect(store.getSnapshot()).toBeTruthy());
    unmount();
    const after = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock.mock.calls.length).toBe(after);
  });
});
