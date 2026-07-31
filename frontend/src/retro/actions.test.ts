/**
 * The write layer.
 *
 * The property worth pinning is that every mutating call feeds the snapshot it
 * gets back into the store — that is what makes your own edits appear
 * immediately instead of waiting for a long-poll to come round — and that the
 * store's monotonic guard is what keeps that safe when a POST response and a
 * parked poll land out of order.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchBody, fetchUrl } from '../test/calls';
import { state } from '../test/retroState';
import { createBoardStore } from '../store/boardStore';
import type { Session } from '../runtime/api';
import type { RetroState } from '../types/board';
import { createRetroActions } from './actions';

const SESSION: Session = { token: 'tok', admin: '', pid: 'pid-1' };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

// A factory, not a Response: a body may only be read once, so a mock that
// resolved the same object twice fails on the second call rather than the
// assertion under test.
function setup(body: () => Response) {
  const fetchMock = vi.fn().mockImplementation(async () => body());
  vi.stubGlobal('fetch', fetchMock);
  const store = createBoardStore<RetroState>();
  return { fetchMock, store, actions: createRetroActions(SESSION, store) };
}

afterEach(() => vi.restoreAllMocks());

describe('createRetroActions', () => {
  it('applies the snapshot the server answers with', async () => {
    const { actions, store } = setup(() => json({ ok: true, state: state({ revision: 7 }) }));
    await actions.addCard('went_well', 'a card', 'Ada');
    expect(store.getSnapshot()?.revision).toBe(7);
  });

  it('drops a response that is older than what the store already holds', async () => {
    // A POST response and a parked long-poll race by design. Without the guard
    // this is the bug you can watch on the current board: move a card, and a
    // response built before the move snaps it back for a beat.
    const { actions, store } = setup(() => json({ ok: true, state: state({ revision: 3 }) }));
    store.apply(state({ revision: 9 }));
    await actions.moveCard('c1', 'demos', 0);
    expect(store.getSnapshot()?.revision).toBe(9);
    expect(store.staleDropped()).toBe(1);
  });

  it('sends the token on the query and the identity in the body', async () => {
    const { actions, fetchMock } = setup(() => json({ ok: true, state: state() }));
    await actions.addCard('demos', '  trimmed  ', 'Ada');

    expect(fetchUrl(fetchMock)).toBe('/api/cards?token=tok');
    expect(fetchBody(fetchMock)).toEqual({
      pid: 'pid-1',
      admin: '',
      grid: 'demos',
      text: 'trimmed',
      author: 'Ada',
    });
  });

  it('never sends a blank card or a blank edit', async () => {
    const { actions, fetchMock } = setup(() => json({ ok: true, state: state() }));
    expect(await actions.addCard('demos', '   ', 'Ada')).toBe(false);
    // An emptied edit box is not a delete — the ✕ is right there, and the
    // server answers 400 anyway. Keeping the card is the safe reading.
    expect(await actions.editCard('c1', '\n  ')).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reports whether a reaction is now yours', async () => {
    const { actions } = setup(() => json({ ok: true, reacted: true, state: state() }));
    expect(await actions.react('c1', '👍')).toBe(true);
  });

  it('reports failure without touching the store when the server refuses', async () => {
    // 403 is the ordinary answer for editing someone else's card, and for a
    // guest who found a host control. It must not look like success.
    const { actions, store } = setup(() => json({ error: 'forbidden' }, 403));
    expect(await actions.deleteCard('c1')).toBe(false);
    expect(store.getSnapshot()).toBeNull();
  });

  it('survives the tunnel dropping mid-request', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const store = createBoardStore<RetroState>();
    const actions = createRetroActions(SESSION, store);
    await expect(actions.setLocked(true)).resolves.toBe(false);
  });

  it('shapes the host broadcasts the way the endpoint expects', async () => {
    const { actions, fetchMock } = setup(() => json({ ok: true, state: state() }));
    await actions.castMusic(true, 2);
    expect(fetchBody(fetchMock)).toMatchObject({ music: { playing: true, channel: 2 } });
    await actions.castTheme('forest');
    expect(fetchBody(fetchMock, 1)).toMatchObject({ theme: 'forest' });
  });
});
