/**
 * Global test setup.
 *
 * Everything here exists because jsdom is not a browser. Each shim below stands
 * in for a real API that a component legitimately uses; none of them fake away
 * behaviour under test.
 */

import { configure } from '@testing-library/dom';
import { cleanup } from '@testing-library/preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, vi } from 'vitest';
import * as matchers from 'vitest-axe/matchers';

/**
 * Make `user-event` flush preact effects, the way `fireEvent` already does.
 *
 * There are **two copies of `@testing-library/dom`** in this tree:
 * `@testing-library/preact` depends on v8 and nests its own, while
 * `user-event` uses the top-level v10. Each carries its own config object.
 * TL-preact configures an `act`-wrapping `eventWrapper` — on its copy. So a
 * `fireEvent` click flushed effects and a `user-event` click did not.
 *
 * The symptom is nasty because nothing errors: the interaction happens, the
 * component re-renders, and only the `useEffect` it should have triggered
 * silently never runs. Anything effect-driven — a document-level key binding, a
 * subscription, a timer — then looks broken in tests and works in a browser.
 *
 * This applies the same wrapper to the copy user-event actually reads.
 */
configure({
  eventWrapper: (cb) => {
    let result: unknown;
    act(() => {
      result = cb();
    });
    return result;
  },
});

// Registers toHaveNoViolations. Its *type* comes from ./vitest-axe.d.ts, not
// from the package: vitest-axe's own augmentation targets the vitest 1.x global
// namespace and is inert under vitest 4.
expect.extend(matchers);

// Unmount between tests. Preact renders into a container testing-library
// creates; without this, a previous test's tree is still in the document and
// `getByRole` finds two of everything.
afterEach(() => cleanup());

/**
 * localStorage.
 *
 * This environment hands out a working `sessionStorage` but an empty `{}` for
 * `localStorage` — no `getItem`, no `clear` — because Node's own experimental
 * `localStorage` (behind `--localstorage-file`) shadows jsdom's when the flag
 * is set without a usable path, which it is here. Since half the runtime's job is
 * remembering who you are across reloads, testing against that would mean
 * testing the unavailable-storage branch and nothing else. This installs a
 * minimal in-memory Storage so the *available* path is exercised too; the
 * unavailable path gets its own test, which replaces the global deliberately.
 */
if (typeof (globalThis.localStorage as Partial<Storage> | undefined)?.getItem !== 'function') {
  const backing = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return backing.size;
    },
    key: (index: number) => [...backing.keys()][index] ?? null,
    getItem: (key: string) => backing.get(key) ?? null,
    setItem: (key: string, value: string) => void backing.set(key, String(value)),
    removeItem: (key: string) => void backing.delete(key),
    clear: () => backing.clear(),
  };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: memory });
}

// matchMedia: used by the theme fallback, the confetti/alarm reduced-motion
// guards, and the visualiser. Defaults to "no preference / dark", matching the
// product default (midnight). Tests that care override it per-case.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

// HTMLMediaElement: jsdom defines play/pause/load and every call prints a "not
// implemented" notice, which every board test triggers because useMusic
// constructs an <audio> on mount and tears it down on unmount. `play()` must
// still return a promise — the autoplay-blocked banner is driven by its
// rejection, so a method returning undefined would break the case rather than
// quieten it. Overridden unconditionally: the methods exist, they just do
// nothing useful here.
HTMLMediaElement.prototype.play = () => Promise.resolve();
HTMLMediaElement.prototype.pause = () => {};
HTMLMediaElement.prototype.load = () => {};

// jsdom defines getContext but throws a "not implemented" notice for every
// call, which floods the output whenever a canvas renders. Both canvas users
// here (confetti, the visualiser) already handle a null context by drawing
// nothing, so returning null is both quiet and the behaviour under test.
// Overridden unconditionally: the method exists, it just does not work.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement['getContext'];
