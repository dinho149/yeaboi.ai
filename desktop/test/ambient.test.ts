// The two parsers main leans on: SSE frames off the ambient feed, and the
// AppleScript answer that says where the Dock is. Everything else in events.ts
// and pet.ts is Electron, which the route tests and a hand-run cover.

import { describe, expect, it, vi } from 'vitest';
import { DOCK_SCRIPT, dockConfig, parseDockRect } from '../src/main/dock';
import { parseFrames } from '../src/main/events';
import { permitted } from '../src/main/permissions';

describe('parseFrames', () => {
  it('reads one whole frame', () => {
    const { events, rest } = parseFrames('data: {"type":"notice","kind":"ceremony_ran"}\n\n');
    expect(events).toEqual([{ type: 'notice', kind: 'ceremony_ran' }]);
    expect(rest).toBe('');
  });

  it('holds a half-arrived frame back until the rest lands', () => {
    const first = parseFrames('data: {"type":"noti');
    expect(first.events).toEqual([]);
    const second = parseFrames(`${first.rest}ce"}\n\n`);
    expect(second.events).toEqual([{ type: 'notice' }]);
  });

  it('reads several frames out of one chunk', () => {
    const { events } = parseFrames('data: {"type":"a"}\n\ndata: {"type":"b"}\n\n');
    expect(events.map((event) => event.type)).toEqual(['a', 'b']);
  });

  it('ignores the keep-alive comments', () => {
    // The feed pings every 15s so a dead peer surfaces as a broken pipe.
    const { events, rest } = parseFrames(': connected\n\n: ping\n\ndata: {"type":"a"}\n\n');
    expect(events.map((event) => event.type)).toEqual(['a']);
    expect(rest).toBe('');
  });

  it('drops an undecodable frame without ending the stream', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { events } = parseFrames('data: {oops\n\ndata: {"type":"a"}\n\n');
    expect(events.map((event) => event.type)).toEqual(['a']);
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });
});

describe('parseDockRect', () => {
  it('reads the four numbers', () => {
    expect(parseDockRect('0,1200,1440,80\n')).toEqual({ x: 0, y: 1200, w: 1440, h: 80 });
  });

  it('is null for anything that is not four numbers', () => {
    // A refused Accessibility permission, a localised error, an empty answer.
    expect(parseDockRect('')).toBeNull();
    expect(parseDockRect('execution error: Not authorised')).toBeNull();
    expect(parseDockRect('0,1200,1440')).toBeNull();
  });

  it('treats a zero-sized dock as no dock', () => {
    // A hidden dock is an edge that is not there to stand on.
    expect(parseDockRect('0,1200,0,0')).toBeNull();
  });

  it('the script asks for position and size in one round-trip', () => {
    expect(DOCK_SCRIPT.join(' ')).toContain('position of list 1');
    expect(DOCK_SCRIPT.join(' ')).toContain('size of list 1');
  });
});

describe('dockConfig', () => {
  it('translates the rect into window-local coordinates', () => {
    const config = dockConfig({ x: 100, y: 1200, w: 1440, h: 80 }, { x: 0, y: -100 });
    expect(config).toEqual({ x: 100, top: 1300, w: 1440, h: 80, present: true });
  });

  it('says so plainly when there is no dock', () => {
    expect(dockConfig(null, { x: 0, y: 0 })).toEqual({ present: false });
  });
});

describe('permitted', () => {
  it('lets the app window ask for the microphone', () => {
    expect(permitted('media', true)).toBe(true);
  });

  it('keeps everything the app window already relied on', () => {
    // Clipboard writes go through a permission check too — an allowlist naming
    // only the microphone would have silently broken every Copy button.
    expect(permitted('clipboard-sanitized-write', true)).toBe(true);
  });

  it('gives a board window nothing', () => {
    // A board page is the same document a teammate opens in a browser. It must
    // not gain a microphone here that it lacks there.
    expect(permitted('media', false)).toBe(false);
    expect(permitted('notifications', false)).toBe(false);
  });
});
