// Wire types + calls for the /api/settings surface (contracts/v1/app_http.md).
// Secrets are write-only over this wire: a secret field's `value` is a masked
// preview, and writes send the raw value through the main-process proxy.

import { apiGet, apiPost } from './api';

export interface SettingField {
  env: string;
  label: string;
  section: string;
  secret: boolean;
  value: string;
  is_set: boolean;
  choices: string[];
  choice_labels: Record<string, string>;
  active_choice: string;
  default: string;
  action: string;
  help_url: string;
  help_scope: string;
}

export interface VoiceDevice {
  index: number;
  name: string;
  channels?: number;
  samplerate?: number;
  is_default: boolean;
}

export interface SettingsSnapshot {
  fields: SettingField[];
  sections: string[];
  config_path: string;
  voice: { state: string; detail: string; devices: VoiceDevice[] };
}

export interface ProviderCard {
  name: string;
  full_name: string;
  env_var: string;
  provider_val: string;
  prefix: string;
  tagline: string;
  instructions: string;
  is_region_input?: boolean;
  is_base_url_input?: boolean;
  default_input?: string;
  models: { default: string; presets: string[] };
  model_hints?: Record<string, string>;
}

export interface ProviderCatalog {
  providers: ProviderCard[];
  anthropic_auth_modes: string[];
  token_help: Record<string, { url: string; scope: string }>;
}

export interface WriteResult {
  ok: boolean;
  key: string;
  message: string;
  restart_required: boolean;
}

export interface SignInStatus {
  active: boolean;
  url?: string;
  awaiting_code?: boolean;
  done?: boolean;
  ok?: boolean;
  saved?: boolean;
  message?: string;
}

export const loadSettings = () => apiGet<SettingsSnapshot>('/api/settings');
export const loadProviders = () => apiGet<ProviderCatalog>('/api/settings/providers');
export const saveSetting = (key: string, value: string) => apiPost<WriteResult>('/api/settings/set', { key, value });
export const saveAllowedPaths = (paths: string[]) => apiPost<WriteResult>('/api/settings/allowed-paths', { paths });
export const saveDataDir = (value: string, move: boolean) =>
  apiPost<WriteResult>('/api/settings/data-dir', { value, move });
export const verifyProvider = (provider: string, credential: string, model = '') =>
  apiPost<{ ok: boolean; message: string }>('/api/settings/provider/verify', { provider, credential, model });
export const discoverModels = (provider: string, credential: string) =>
  apiPost<{ models: string[]; default: string; hints: Record<string, string> }>('/api/settings/provider/models', {
    provider,
    credential,
  });
export const signInStart = () => apiPost<{ started: boolean; message: string }>('/api/settings/signin/start');
export const signInStatus = () => apiGet<SignInStatus>('/api/settings/signin');
export const signInCode = (code: string) => apiPost<{ ok: boolean }>('/api/settings/signin/code', { code });
export const signInCancel = () => apiPost<{ ok: boolean }>('/api/settings/signin/cancel');
