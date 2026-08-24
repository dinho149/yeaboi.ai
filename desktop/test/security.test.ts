// The two rules that decide what the shell will let through: where a window
// may navigate, and which backend routes the renderer may ask the proxy for.
// Both are pure, so neither needs Electron to be tested.

import { describe, expect, it, vi } from 'vitest';
import { navigationAllowed } from '../src/main/permissions';

// api-proxy imports ipcMain as a value, and CI installs no Electron binary —
// so the real module throws on import and takes the whole file with it.
vi.mock('electron', () => ({ ipcMain: { handle: () => {} } }));
const { rendererMayCall } = await import('../src/main/api-proxy');

describe('navigationAllowed', () => {
  it('allows the loopback backend on any port', () => {
    // The backend and every board server bind an ephemeral port, so the host
    // is what can be checked here, not the port.
    expect(navigationAllowed('http://127.0.0.1:8731/api/meta/version')).toBe(true);
    expect(navigationAllowed('http://localhost:5173/')).toBe(true);
  });

  it('refuses a host that merely starts with the loopback address', () => {
    // A prefix test would accept this, and a page that navigated there would
    // still hold the preload bridge.
    expect(navigationAllowed('http://127.0.0.1.attacker.example/')).toBe(false);
    expect(navigationAllowed('http://127.0.0.1evil.example/')).toBe(false);
  });

  it('refuses the outside world and unparseable input', () => {
    expect(navigationAllowed('https://example.com/')).toBe(false);
    expect(navigationAllowed('file:///etc/passwd')).toBe(false);
    expect(navigationAllowed('not a url')).toBe(false);
  });

  it('allows the dev server it was given, by origin', () => {
    expect(navigationAllowed('http://dev.internal:5399/#/home', 'http://dev.internal:5399/')).toBe(true);
    expect(navigationAllowed('http://other.internal:5399/', 'http://dev.internal:5399/')).toBe(false);
  });

  it('refuses everything else when no dev server is running', () => {
    expect(navigationAllowed('https://yeaboi.ai/')).toBe(false);
  });
});

describe('rendererMayCall', () => {
  it('allows the ordinary api', () => {
    expect(rendererMayCall('/api/boards')).toBe(true);
    expect(rendererMayCall('/api/boards/b1')).toBe(true);
    expect(rendererMayCall('/api/chat/send')).toBe(true);
  });

  it('refuses the board host link, which carries the admin token', () => {
    expect(rendererMayCall('/api/boards/abc123/host')).toBe(false);
    expect(rendererMayCall('/api/boards/abc123/host?x=1')).toBe(false);
  });
});
