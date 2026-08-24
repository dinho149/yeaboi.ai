// Typed access to the backend through the preload bridge. The renderer never
// sees the loopback URL or the bearer token — window.yeaboi.api is a blind
// relay whose auth lives in the main process.

export interface Envelope<T = unknown> {
  ok: boolean;
  llm_mode: string;
  warnings: string[];
  data: T;
  error?: { type: string; message: string };
  hint?: string;
}

interface Bridge {
  api: (path: string, init?: { method?: string; body?: unknown }) => Promise<{ status: number; body: unknown }>;
  apiStream: (
    path: string,
    body: unknown,
    onLine: (line: unknown) => void,
  ) => Promise<{ status: number; body: unknown }>;
  getBackendState: () => Promise<unknown>;
  onBackendState: (callback: (state: unknown) => void) => void;
  onEvent: (callback: (event: unknown) => void) => void;
  onNavigate: (callback: (route: string) => void) => void;
  setPetEnabled: (enabled: boolean) => Promise<unknown>;
  appMeta: () => Promise<unknown>;
  onUpdateState: (callback: (state: unknown) => void) => void;
  getUpdateState: () => Promise<unknown>;
  checkForUpdate: () => Promise<unknown>;
  downloadUpdate: () => Promise<unknown>;
  installUpdate: () => Promise<unknown>;
  onAbout: (callback: () => void) => void;
  platform: string;
}

function bridge(): Bridge {
  const found = (window as unknown as { yeaboi?: Bridge }).yeaboi;
  if (!found) throw new Error('preload bridge missing — renderer loaded outside Electron?');
  return found;
}

export async function apiGet<T>(path: string): Promise<T> {
  const { status, body } = await bridge().api(path);
  if (status !== 200) throw new Error((body as { error?: string }).error ?? `GET ${path} → ${status}`);
  return body as T;
}

export async function apiPost<T>(path: string, body: object = {}): Promise<T> {
  const { status, body: resp } = await bridge().api(path, { method: 'POST', body });
  if (status !== 200) throw new Error((resp as { error?: string }).error ?? `POST ${path} → ${status}`);
  return resp as T;
}

/** POST a request whose response is NDJSON, calling back once per parsed line. */
export async function apiStream(path: string, body: object, onLine: (line: unknown) => void): Promise<void> {
  const { status, body: resp } = await bridge().apiStream(path, body, onLine);
  if (status !== 200) throw new Error((resp as { error?: string }).error ?? `POST ${path} → ${status}`);
}

export async function callTool<T = unknown>(name: string, args: object = {}): Promise<Envelope<T>> {
  const { status, body } = await bridge().api(`/api/tool/${name}`, {
    method: 'POST',
    body: { arguments: args },
  });
  if (status !== 200) throw new Error((body as { error?: string }).error ?? `tool ${name} → ${status}`);
  return body as Envelope<T>;
}

export function onBackendState(callback: (state: { kind: string; reason?: string }) => void): void {
  bridge().onBackendState((state) => callback(state as { kind: string; reason?: string }));
}

export function getBackendState(): Promise<{ kind: string; reason?: string }> {
  return bridge().getBackendState() as Promise<{ kind: string; reason?: string }>;
}

/** The ambient feed: consent requests and awareness notices, read once in main
 *  and pushed here. Not a second subscription — main owns the only one. */
export function onAmbientEvent(callback: (event: { type: string; [key: string]: unknown }) => void): void {
  bridge().onEvent((event) => callback(event as { type: string; [key: string]: unknown }));
}

/** Main asking the window to show a route — the tray, or a click on the duck. */
export function onNavigate(callback: (route: string) => void): void {
  bridge().onNavigate(callback);
}

/** The desktop pet's on/off. Main owns the window; the backend owns the choice. */
export function setPetEnabled(enabled: boolean): Promise<unknown> {
  return bridge().setPetEnabled(enabled);
}

/** 'darwin' | 'win32' | 'linux' — what the shortcut sheet names its modifier. */
export function platform(): string {
  return bridge().platform;
}

export interface VersionMeta {
  version: string;
  schema_version: number;
  python: string;
}

export const getVersion = (): Promise<VersionMeta> => apiGet<VersionMeta>('/api/meta/version');

/** The shell's own versions. Not a backend call — main is the only thing that
 *  knows what Electron build this is or whether the app is packaged. */
export interface ShellMeta {
  version: string;
  electron: string;
  chrome: string;
  platform: string;
  arch: string;
  packaged: boolean;
}

export const getShellMeta = (): Promise<ShellMeta> => bridge().appMeta() as Promise<ShellMeta>;

export type UpdateState =
  | { kind: 'unsupported'; reason: string }
  | { kind: 'idle'; version?: string }
  | { kind: 'checking' }
  | { kind: 'available'; version: string }
  | { kind: 'downloading'; version: string; percent: number }
  | { kind: 'ready'; version: string }
  | { kind: 'error'; message: string };

export const getUpdateState = (): Promise<UpdateState> => bridge().getUpdateState() as Promise<UpdateState>;
export const checkForUpdate = (): Promise<UpdateState> => bridge().checkForUpdate() as Promise<UpdateState>;
export const downloadUpdate = (): Promise<UpdateState> => bridge().downloadUpdate() as Promise<UpdateState>;
export const installUpdate = (): Promise<unknown> => bridge().installUpdate();

export function onUpdateState(callback: (state: UpdateState) => void): void {
  bridge().onUpdateState((state) => callback(state as UpdateState));
}

/** The tray asking for the About panel. */
export function onAbout(callback: () => void): void {
  bridge().onAbout(callback);
}
