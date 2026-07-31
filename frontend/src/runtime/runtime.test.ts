/**
 * The small runtime modules: storage, boot payload, formatting, names.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BOOT_ELEMENT_ID, readBoot, requireBoot } from './boot';
import { cx } from './cx';
import { fmtAgo, fmtClock, initials, nameDigest } from './format';
import { randomName } from './random';
import { participantId, read, readEnum, remove, write } from './storage';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  document.body.innerHTML = '';
});
afterEach(() => {
  // unstubAllGlobals as well as restoreAllMocks: the storage and crypto cases
  // below replace globals, and a leaked replacement breaks every later test in
  // the file rather than failing where the mistake is.
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('storage', () => {
  it('round-trips a value', () => {
    write('local', 'k', 'v');
    expect(read('local', 'k')).toBe('v');
    remove('local', 'k');
    expect(read('local', 'k')).toBeNull();
  });

  it('keeps local and session apart', () => {
    // Identity in local (shared across tabs), access in session (per tab). The
    // separation is the reason cookies are rejected outright.
    write('local', 'k', 'local');
    write('session', 'k', 'session');
    expect(read('local', 'k')).toBe('local');
    expect(read('session', 'k')).toBe('session');
  });

  it('survives storage throwing on access', () => {
    // Safari private mode and file:// with third-party storage blocked throw on
    // the *property read*, not on the write. One unguarded access takes the
    // page down before it paints, and exports are opened over file:// routinely.
    // Saved and restored by hand rather than with vi.stubGlobal: `localStorage`
    // is an accessor on the window prototype, and the stub helper cannot put
    // that back — leaving every later test in the file talking to the fake.
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
    });

    try {
      expect(read('local', 'k')).toBeNull();
      expect(() => write('local', 'k', 'v')).not.toThrow();
      expect(() => remove('local', 'k')).not.toThrow();
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original);
      else Reflect.deleteProperty(window, 'localStorage');
    }
  });

  it('falls back when a stored value is not in the allowlist', () => {
    // These come back from a store the user can edit in devtools; the server
    // re-validates, this just keeps the client from rendering something broken.
    write('local', 'theme', 'chartreuse');
    expect(readEnum('local', 'theme', ['midnight', 'light'] as const, 'midnight')).toBe('midnight');
    write('local', 'theme', 'light');
    expect(readEnum('local', 'theme', ['midnight', 'light'] as const, 'midnight')).toBe('light');
  });
});

describe('participantId', () => {
  it('mints once and reuses', () => {
    const first = participantId();
    expect(first).toBeTruthy();
    expect(participantId()).toBe(first);
  });

  it('works without crypto.randomUUID', () => {
    // randomUUID is only exposed in a secure context. Every route to the board
    // is one now, so this fallback is rare rather than common — but a pid that
    // throws is a participant who cannot vote.
    vi.stubGlobal('crypto', {});
    const id = participantId();
    expect(id).toMatch(/^p[a-z0-9]+$/);
    expect(participantId()).toBe(id);
  });
});

describe('readBoot', () => {
  function island(text: string): void {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = BOOT_ELEMENT_ID;
    el.textContent = text;
    document.body.appendChild(el);
  }

  it('returns null when there is no payload', () => {
    // A real answer, not an error: the join gate ships with no island at all,
    // so an unauthenticated visitor learns nothing about what is behind it.
    expect(readBoot()).toBeNull();
  });

  it('parses the payload', () => {
    island('{"grids":["went_well"],"n":3}');
    expect(readBoot()).toEqual({ grids: ['went_well'], n: 3 });
  });

  it('throws on malformed JSON rather than booting empty', () => {
    // A board that silently starts with no config is far harder to diagnose
    // than a console error naming the element.
    island('{not json');
    expect(() => readBoot()).toThrow(/not valid JSON/);
  });

  it('requireBoot names the mistake', () => {
    expect(() => requireBoot()).toThrow(/render_page was called without data/);
  });
});

describe('format', () => {
  const now = Date.parse('2026-07-30T12:00:00Z');

  it.each([
    ['2026-07-30T11:59:55Z', 'just now'],
    ['2026-07-30T11:59:20Z', '40s'],
    ['2026-07-30T11:53:00Z', '7m'],
  ])('formats %s as %s', (iso, label) => {
    expect(fmtAgo(iso, now)?.label).toBe(label);
  });

  it('returns null for an unparseable timestamp', () => {
    // The old implementation produced "NaN" on screen for this.
    expect(fmtAgo('not a date', now)).toBeNull();
  });

  it.each([
    [0, '00:00'],
    [9, '00:09'],
    [95, '01:35'],
    [-5, '00:00'],
  ])('formats %s seconds as %s', (seconds, text) => {
    expect(fmtClock(seconds)).toBe(text);
  });

  it.each([
    ['Alice Johnson', 'AJ'],
    ['alice', 'A'],
    ['Ada B. Lovelace', 'AL'],
    ['', '?'],
    ['🙂', '?'],
  ])('initials of %s are %s', (name, expected) => {
    expect(initials(name)).toBe(expected);
  });

  it('digests a name the same way Python does', () => {
    // A plain sum of code points, deliberately not a real hash: the same person
    // must get the same avatar colour in the browser and in an export, and
    // Python's built-in hash() is salted per process.
    expect(nameDigest('AB')).toBe(65 + 66);
    expect(nameDigest('')).toBe(0);
  });
});

describe('randomName', () => {
  it('combines an adjective and a noun', () => {
    expect(randomName(['Brave'], ['Otter'], () => 0)).toBe('Brave Otter');
  });

  it('falls back when the word lists are empty', () => {
    // The lists arrive in the boot payload; an older server could send neither.
    expect(randomName([], [])).toBe('Guest');
  });
});

describe('cx', () => {
  it('drops falsy parts, which is the whole reason it exists', () => {
    // CSS Modules are an index signature and noUncheckedIndexedAccess makes
    // styles.foo `string | undefined`; a template literal would interpolate
    // the literal text "undefined" into the className.
    expect(cx('a', undefined, false, null, 'b')).toBe('a b');
  });
});
