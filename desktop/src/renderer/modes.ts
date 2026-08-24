// The reporting, performance, roadmap and ship wire — the shapes
// contracts/v1/app_http.md pins, the calls that produce them, and the pure
// reducers over a run's NDJSON lines (plain functions, so vitest can drive
// them without a DOM).
//
// No rule is re-declared here. Which period earns which step, which sprint
// selection makes a quarter "custom", how large a roadmap project has to be
// for the full intake, and what a repo resolves to are all backend answers —
// a second copy of any of them is a second thing to drift.

import { type Envelope, apiGet, apiPost, apiStream, callTool } from './api';

// ── A streamed run ─────────────────────────────────────────────────────────

export interface ModeRunState {
  opId: string;
  phases: string[];
  /** The whole `done` line — a report, an analysis, whatever the run yields. */
  done: Record<string, unknown> | null;
  error: string;
  cancelled: boolean;
  finished: boolean;
}

export function emptyModeRun(): ModeRunState {
  return { opId: '', phases: [], done: null, error: '', cancelled: false, finished: false };
}

/** Fold one NDJSON line into the run's state. Returns a new object. */
export function reduceModeRun(state: ModeRunState, line: unknown): ModeRunState {
  const row = (line ?? {}) as Record<string, unknown>;
  switch (row.type) {
    case 'op':
      return { ...state, opId: String(row.op_id ?? '') };
    case 'progress':
      return { ...state, phases: [...state.phases, String(row.phase ?? '')] };
    case 'done':
      return { ...state, done: row, finished: true };
    case 'cancelled':
      return { ...state, cancelled: true, finished: true };
    case 'error':
      return { ...state, error: String(row.message ?? 'The run stopped.'), finished: true };
    default:
      // An unknown line type is a newer backend, not a failure — ignore it.
      return state;
  }
}

export function cancelModeRun(opId: string): Promise<unknown> {
  return apiPost(`/api/ops/${encodeURIComponent(opId)}/cancel`);
}

// ── Reporting ──────────────────────────────────────────────────────────────

export interface PeriodOption {
  key: string;
  label: string;
  description: string;
}

export interface DeckStyle {
  [field: string]: string | number | boolean;
}

export interface StyleField {
  key: string;
  label: string;
  kind: string;
}

export interface ReportingOptions {
  periods: PeriodOption[];
  sources: {
    grid: Record<string, string[]>;
    step_applies: boolean;
    descriptions: Record<string, string>;
    titles: Record<string, string>;
    summary: string;
  };
  themes: string[];
  palettes: Record<string, Record<string, string>>;
  style: DeckStyle;
  style_summary: string;
  style_fields: StyleField[];
  style_choices: {
    color_roles: string[];
    fonts: string[];
    font_scales: string[];
    layouts: string[];
    content_fits: string[];
    content_fit_labels: Record<string, string>;
    max_bullets: number[];
  };
  default_window: { start: string; end: string };
}

export interface SprintRow {
  name: string;
  start_date: string;
  end_date: string;
  source: string;
  in_quarter: boolean;
}

export interface ReportWindow {
  window_start: string;
  window_end: string;
  sprint_names: string[];
  period_label_override: string;
}

export interface SprintList {
  sprints: SprintRow[];
  checked: number[];
  fallback: ReportWindow;
}

export interface ReportRun {
  id: number;
  run_at: string;
  period: string;
  period_end: string;
  project_name: string;
  item_count: number;
}

export const loadReportingOptions = (): Promise<ReportingOptions> => apiGet('/api/reporting/options');

export const loadSprints = (sessionId: string): Promise<SprintList> =>
  apiGet(`/api/reporting/sprints?session_id=${encodeURIComponent(sessionId)}`);

export const resolveWindow = (sprints: SprintRow[], checked: number[]): Promise<ReportWindow> =>
  apiPost('/api/reporting/window', { sprints, checked });

export const saveDeckStyle = (style: DeckStyle): Promise<{ style: DeckStyle; style_summary: string }> =>
  apiPost('/api/reporting/style', { style });

export const resetDeckStyle = (): Promise<{ style: DeckStyle; style_summary: string }> =>
  apiPost('/api/reporting/style', { reset: true });

export const askFit = (
  ref: { session_id: string; run_id: number },
  style: DeckStyle,
): Promise<{ extra_slides: number; style: DeckStyle }> => apiPost('/api/reporting/fit', { ...ref, style });

export const exportDeck = (body: Record<string, unknown>): Promise<{ paths: Record<string, string> }> =>
  apiPost('/api/reporting/export', body);

export const runReport = (body: Record<string, unknown>, onLine: (line: unknown) => void): Promise<void> =>
  apiStream('/api/reporting/run', body, onLine);

export const reportingHistory = (): Promise<Envelope<{ history: ReportRun[] }>> => callTool('reporting_history', {});

// ── Performance ────────────────────────────────────────────────────────────

export interface EngineerRow {
  name: string;
  hint: string;
}

export interface PerformanceRoster {
  session_id: string;
  session_name: string;
  engineers: EngineerRow[];
  actions: { key: string; label: string }[];
  empty_message: string;
}

export interface EngineerFile {
  engineer: string;
  prep: Record<string, unknown> | null;
  review: Record<string, unknown> | null;
  completions: Record<string, unknown>[];
  open_actions: string[];
  notes: { note: string; created_at: string }[];
  history: Record<string, unknown>[];
  latest: { title: string; artifact_kind: string; artifact: Record<string, unknown> } | null;
}

export const loadPerformanceRoster = (): Promise<PerformanceRoster> => apiGet('/api/performance/roster');

export const loadEngineer = (name: string): Promise<EngineerFile> =>
  apiGet(`/api/performance/engineer/${encodeURIComponent(name)}`);

export const runPrep = (engineer: string, sessionId: string): Promise<Envelope<Record<string, unknown>>> =>
  callTool('perf_one_on_one_prep', { engineer, session_id: sessionId });

export const completeOneOnOne = (
  engineer: string,
  transcript: string,
  sessionId: string,
): Promise<Envelope<Record<string, unknown>>> =>
  callTool('perf_one_on_one_complete', { engineer, transcript, session_id: sessionId });

export const runReview = (engineer: string, sessionId: string): Promise<Envelope<Record<string, unknown>>> =>
  callTool('perf_six_month_review', { engineer, session_id: sessionId });

export const addNote = (engineer: string, note: string): Promise<Envelope<Record<string, unknown>>> =>
  callTool('perf_note_add', { engineer, note_text: note });

// ── Roadmap ────────────────────────────────────────────────────────────────

export interface RoadmapSourceOption {
  key: string;
  label: string;
  hint: string;
  configured: boolean;
  prompt: string;
}

export interface RoadmapProjectRow {
  name: string;
  description: string;
  size: string;
  rationale?: string;
}

export interface RoadmapAnalysisView {
  source_type: string;
  source_label: string;
  summary: string;
  projects: RoadmapProjectRow[];
  warnings: string[];
  generated_at: string;
}

export interface SavedRoadmap {
  id: number;
  label: string;
  source_type: string;
  analyzed_at: string;
  project_count: number;
}

export const loadRoadmapOptions = (): Promise<{ sources: RoadmapSourceOption[] }> => apiGet('/api/roadmap/options');

export const loadSavedRoadmaps = (): Promise<{ roadmaps: SavedRoadmap[] }> => apiGet('/api/roadmap/saved');

export const loadRoadmap = (id: number): Promise<{ roadmap: Record<string, unknown> }> =>
  apiGet(`/api/roadmap/saved/${id}`);

export const analyzeRoadmap = (
  body: { source_type: string; locator: string; roadmap_id?: number },
  onLine: (line: unknown) => void,
): Promise<void> => apiStream('/api/roadmap/analyze', body, onLine);

export const planProject = (
  roadmapId: number,
  index: number,
): Promise<{ intake_mode: string; description: string }> => apiPost('/api/roadmap/plan', { roadmap_id: roadmapId, index });

// ── Ship ───────────────────────────────────────────────────────────────────

export interface StoryRow {
  id: string;
  title: string;
  points: number;
  criteria: number;
}

export interface ShipStories {
  stories: StoryRow[];
  session_id: string;
  project_name: string;
  problem: string;
  empty_message: string;
  default_repo: string;
}

export interface ShipTarget {
  repo: string;
  problem: string;
  allowed: boolean;
  consent_hint: string;
}

export interface ShipPhase {
  component_id: string;
  label: string;
  status: string;
  detail?: string;
}

export interface ShipGate {
  run_id: string;
  story_id: string;
  branch: string;
  diff_stat: string;
  diff_text: string;
  cost_usd: number;
  validation: Record<string, unknown>;
}

export interface ShipSnapshot {
  key: string;
  run_id: string;
  story_id: string;
  story_title: string;
  repo: string;
  check_command: string;
  started_at: string;
  finished: boolean;
  cancelling: boolean;
  phases: ShipPhase[];
  gate: ShipGate | null;
  result: Record<string, unknown> | null;
  failure: string;
  board: { url: string; code: string };
}

export const loadStories = (): Promise<ShipStories> => apiGet('/api/ship/stories');

export const resolveRepo = (repo: string): Promise<ShipTarget> => apiPost('/api/ship/target', { repo });

export const loadShipRuns = (): Promise<{ runs: ShipSnapshot[] }> => apiGet('/api/ship/runs');

export const loadShipRun = (key: string): Promise<ShipSnapshot> =>
  apiGet(`/api/ship/runs/${encodeURIComponent(key)}`);

export const launchShip = (body: Record<string, unknown>): Promise<ShipSnapshot> => apiPost('/api/ship/runs', body);

export const answerGate = (
  key: string,
  resolution: 'approved' | 'rejected',
  comment = '',
): Promise<{ taken: boolean; resolution: string }> =>
  apiPost(`/api/ship/runs/${encodeURIComponent(key)}/gate`, { resolution, comment });

export const cancelShip = (key: string): Promise<{ cancelling: boolean }> =>
  apiPost(`/api/ship/runs/${encodeURIComponent(key)}/cancel`);

/** The run key in `#/humans/ship/run?key=…`, or '' when the hash carries none. */
export function shipKeyFromHash(hash: string): string {
  const query = hash.split('?')[1] ?? '';
  return new URLSearchParams(query).get('key') ?? '';
}

/** The numeric query value in a hash, or 0 — for `?run_id=` and `?id=`. */
export function numberFromHash(hash: string, name: string): number {
  const query = hash.split('?')[1] ?? '';
  const raw = new URLSearchParams(query).get(name) ?? '';
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : 0;
}
