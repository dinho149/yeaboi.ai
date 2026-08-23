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
  getBackendState: () => Promise<unknown>;
  onBackendState: (callback: (state: unknown) => void) => void;
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
