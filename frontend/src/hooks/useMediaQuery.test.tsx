/**
 * The density switch's only input.
 *
 * Worth its own file because the failure modes are environmental rather than
 * logical: a jsdom with no `matchMedia`, and a Safari old enough to have only
 * the deprecated listener API. Both render a board; neither can be reproduced
 * by reading the hook.
 */

import { render, screen, act } from '@testing-library/preact';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useMediaQuery } from './useMediaQuery';

const QUERY = '(min-width: 1100px)';

function Probe() {
  return <span data-testid="matches">{String(useMediaQuery(QUERY))}</span>;
}

const matches = (): string | null => screen.getByTestId('matches').textContent;

/** A `matchMedia` whose result can be changed, with listeners that fire. */
function stubMatchMedia({ initial, legacy = false }: { initial: boolean; legacy?: boolean }) {
  const listeners = new Set<() => void>();
  let current = initial;
  const list = {
    get matches() {
      return current;
    },
    media: QUERY,
    ...(legacy
      ? {
          addEventListener: undefined,
          addListener: (fn: () => void) => void listeners.add(fn),
          removeListener: (fn: () => void) => void listeners.delete(fn),
        }
      : {
          addEventListener: (_: string, fn: () => void) => void listeners.add(fn),
          removeEventListener: (_: string, fn: () => void) => void listeners.delete(fn),
        }),
  };
  const original = window.matchMedia;
  window.matchMedia = (() => list) as unknown as typeof window.matchMedia;
  return {
    restore: () => void (window.matchMedia = original),
    set(next: boolean) {
      current = next;
      act(() => listeners.forEach((fn) => fn()));
    },
    get listenerCount() {
      return listeners.size;
    },
  };
}

describe('useMediaQuery', () => {
  let stub: ReturnType<typeof stubMatchMedia> | null = null;
  afterEach(() => {
    stub?.restore();
    stub = null;
    vi.restoreAllMocks();
  });

  it('reads the current value during the first render', () => {
    // Not "renders false then corrects itself" — on a board that is a visible
    // jump from the compact masthead to the hero one on every load.
    stub = stubMatchMedia({ initial: true });
    render(<Probe />);
    expect(matches()).toBe('true');
  });

  it('re-renders when the query starts matching', () => {
    stub = stubMatchMedia({ initial: false });
    render(<Probe />);
    expect(matches()).toBe('false');

    stub.set(true);
    expect(matches()).toBe('true');
  });

  it('unsubscribes on unmount', () => {
    stub = stubMatchMedia({ initial: false });
    const { unmount } = render(<Probe />);
    expect(stub.listenerCount).toBe(1);

    unmount();
    expect(stub.listenerCount).toBe(0);
  });

  it('falls back to the deprecated listener API', () => {
    // Safari below 14 has only addListener, and a tunnel link lands on whatever
    // phone the teammate owns.
    stub = stubMatchMedia({ initial: false, legacy: true });
    render(<Probe />);
    expect(matches()).toBe('false');

    stub.set(true);
    expect(matches()).toBe('true');
  });

  it('reports false where matchMedia does not exist at all', () => {
    // jsdom without the setup stub, and the reason every board test would
    // otherwise throw rather than fail.
    const original = window.matchMedia;
    // @ts-expect-error - deleting a DOM API is the condition under test
    delete window.matchMedia;
    try {
      render(<Probe />);
      expect(matches()).toBe('false');
    } finally {
      window.matchMedia = original;
    }
  });
});
