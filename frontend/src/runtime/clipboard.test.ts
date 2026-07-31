/**
 * The clipboard's two paths.
 *
 * The fallback is the reason this file exists. `navigator.clipboard` is gated on
 * a secure context, and one of the five bundles is opened from disk — a static
 * export is a `file://` document, which browsers treat as not secure. On that
 * surface the modern API is either absent or rejects, and `execCommand` is the
 * only thing left. It is easy to write the fallback, never exercise it, and ship
 * a copy button that silently does nothing in exports for a year.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { copyText } from './clipboard';

const original = navigator.clipboard;

function setClipboard(value: unknown): void {
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true, writable: true });
}

beforeEach(() => {
  // jsdom has no execCommand at all, so it is defined per-test rather than spied.
  (document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => true);
});

afterEach(() => {
  setClipboard(original);
  vi.restoreAllMocks();
});

describe('copyText', () => {
  it('uses the modern API when there is one', async () => {
    const writeText = vi.fn(async () => undefined);
    setClipboard({ writeText });

    await expect(copyText('hello')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
    expect(document.execCommand).not.toHaveBeenCalled();
  });

  it('falls back when the API is missing — the file:// export case', async () => {
    setClipboard(undefined);

    await expect(copyText('hello')).resolves.toBe(true);
    expect(document.execCommand).toHaveBeenCalledWith('copy');
  });

  it('falls back when the API rejects — a denied or stale gesture', async () => {
    setClipboard({ writeText: vi.fn(async () => Promise.reject(new Error('denied'))) });

    await expect(copyText('hello')).resolves.toBe(true);
    expect(document.execCommand).toHaveBeenCalledWith('copy');
  });

  it('reports failure rather than throwing when both paths fail', async () => {
    // Every caller shows the value and lets the reader select it; none of them
    // can do anything with an exception.
    setClipboard(undefined);
    (document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => false);

    await expect(copyText('hello')).resolves.toBe(false);
  });

  it('leaves no scratch element behind', async () => {
    // The fallback has to attach a real, selectable textarea to the document.
    // One left in the DOM would be a stray tab stop on every board.
    setClipboard(undefined);
    const before = document.body.childElementCount;

    await copyText('hello');

    expect(document.body.childElementCount).toBe(before);
  });

  it('does nothing for an empty string', async () => {
    const writeText = vi.fn(async () => undefined);
    setClipboard({ writeText });

    await expect(copyText('')).resolves.toBe(false);
    expect(writeText).not.toHaveBeenCalled();
  });
});
