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
