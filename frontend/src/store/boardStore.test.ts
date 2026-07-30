/**
 * The monotonic guard, which exists to fix a bug you can reproduce today:
 * drag a card to another column on the polling board and it briefly snaps back,
 * because a poll response the server built *before* the move lands afterwards.
 */

import { describe, expect, it, vi } from 'vitest';

import { createBoardStore } from './boardStore';

interface Snap {
  revision: number;
  label: string;
}

describe('createBoardStore', () => {
  it('starts empty', () => {
    expect(createBoardStore<Snap>().getSnapshot()).toBeNull();
  });

  it('accepts a newer snapshot and notifies', () => {
    const store = createBoardStore<Snap>();
    const listener = vi.fn();
    store.subscribe(listener);

    expect(store.apply({ revision: 1, label: 'a' })).toBe(true);
    expect(store.getSnapshot()).toEqual({ revision: 1, label: 'a' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('drops a snapshot from before the one it holds', () => {
    const store = createBoardStore<Snap>();
    store.apply({ revision: 7, label: 'moved' });
    const listener = vi.fn();
    store.subscribe(listener);

    expect(store.apply({ revision: 6, label: 'stale' })).toBe(false);
    expect(store.getSnapshot()?.label).toBe('moved');
    expect(listener).not.toHaveBeenCalled();
    expect(store.staleDropped()).toBe(1);
  });

  it('accepts an equal revision, because presence does not bump it', () => {
    // RetroBoard.heartbeat deliberately leaves `revision` alone — heartbeats
    // fire about once a second and bumping would defeat change detection. Drop
    // equal revisions and the who's-here row freezes.
    const store = createBoardStore<Snap>();
    store.apply({ revision: 3, label: 'alice' });
    expect(store.apply({ revision: 3, label: 'alice, bob' })).toBe(true);
    expect(store.getSnapshot()?.label).toBe('alice, bob');
  });

  it('survives a listener unsubscribing during notification', () => {
    // React does exactly this when a subscribed component unmounts mid-update.
    // Iterating the live Set would silently skip the next listener.
    const store = createBoardStore<Snap>();
    const second = vi.fn();
    const unsubscribeFirst = store.subscribe(() => unsubscribeFirst());
    store.subscribe(second);

    store.apply({ revision: 1, label: 'a' });
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('stops notifying after unsubscribe', () => {
    const store = createBoardStore<Snap>();
    const listener = vi.fn();
    store.subscribe(listener)();
    store.apply({ revision: 1, label: 'a' });
    expect(listener).not.toHaveBeenCalled();
  });

  it('reset clears the snapshot so a different board starts clean', () => {
    const store = createBoardStore<Snap>();
    store.apply({ revision: 9, label: 'old board' });
    store.reset();

    expect(store.getSnapshot()).toBeNull();
    // And a low revision is accepted again — the new board's numbering is
    // unrelated to the old one's, so the guard must not carry across.
    expect(store.apply({ revision: 1, label: 'new board' })).toBe(true);
  });
});
