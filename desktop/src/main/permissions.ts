// What a window in this app may ask the OS for.
//
// Electron grants every permission request when no handler is set, which was
// survivable while nothing here wanted one. Now something does: dictation opens
// the microphone. A blanket yes is a poor neighbour for that, because the app
// draws windows it did not write — a live board is the same document a teammate
// opens in a browser, on its own partition, and it must not gain a microphone
// here that it lacks there.
//
// So the rule is about *which window is asking*, not which permission. The app's
// own window keeps what it has: it is our bundle, behind a CSP that lets it talk
// to nothing but the local backend, and an allowlist naming only the microphone
// would have quietly broken every Copy button. Every other window — boards and
// the pet — gets nothing, which is what each of them needs.
//
// The electron objects arrive as arguments, so the rule is testable without one.

import type { WebContents } from 'electron';

/** Pure so the rule is testable without a browser: is this ask allowed? */
export function permitted(_permission: string, fromAppWindow: boolean): boolean {
  return fromAppWindow;
}

/**
 * Where a window in this app may navigate: the loopback backend, or the dev
 * server when one is running. Everything else is refused and, if it came from
 * a link, opened in the OS browser instead.
 *
 * Compared by parsed host and origin rather than by string prefix. A prefix
 * test on `http://127.0.0.1` also accepts `http://127.0.0.1.example.com/`,
 * and a page that navigated there would still hold the preload bridge — an
 * authed path to settings writes, exports and ship launches.
 */
export function navigationAllowed(url: string, devServerUrl?: string): boolean {
  let target: URL;
  try {
    target = new URL(url);
  } catch {
    return false;
  }
  if (target.protocol === 'http:' && (target.hostname === '127.0.0.1' || target.hostname === 'localhost')) return true;
  if (!devServerUrl) return false;
  try {
    // The dev server's own hash routes are the app navigating within itself.
    return new URL(devServerUrl).origin === target.origin;
  } catch {
    return false;
  }
}

/** The slice of a session this module touches. */
export interface PermissionSession {
  setPermissionRequestHandler(
    handler: (contents: WebContents | null, permission: string, callback: (allowed: boolean) => void) => void,
  ): void;
  setPermissionCheckHandler(handler: (contents: WebContents | null, permission: string) => boolean): void;
}

/**
 * Apply the rule to every session this app has or will create.
 *
 * Both halves are needed. `session-created` catches the `board:<id>` partitions,
 * which are made on demand and would otherwise each start life with a default of
 * yes-to-everything; the default session is handled directly because it already
 * exists by the time this runs.
 */
export function installPermissionHandlers(
  onSessionCreated: (listener: (created: PermissionSession) => void) => void,
  defaultSession: PermissionSession,
  isAppWindow: (contents: WebContents | null) => boolean,
): void {
  const apply = (target: PermissionSession): void => {
    target.setPermissionRequestHandler((contents, permission, callback) => {
      const allowed = permitted(permission, isAppWindow(contents));
      if (!allowed) console.log(`[permissions] refused ${permission} outside the app window`);
      callback(allowed);
    });
    // The synchronous half — getUserMedia consults this before it prompts, and
    // it is what decides whether device labels are visible at all.
    target.setPermissionCheckHandler((contents, permission) => permitted(permission, isAppWindow(contents)));
  };
  onSessionCreated(apply);
  apply(defaultSession);
}
