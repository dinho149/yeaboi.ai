// The standup and analysis dashboards' wire — the shapes
// contracts/v1/app_http.md pins, the calls that produce them, and the pure
// reducers over a run's NDJSON lines (plain functions, so vitest can drive
// them without a DOM).
//
// The card vocabulary is NOT re-declared here: the backend sends the cards a
// result earned, in order, because the terminal and the desktop must agree on
// what a dashboard contains.

import { apiGet, apiPost, apiStream, callTool } from './api';

export interface DashboardCard {
  key: string;
  title: string;
  /** Non-empty only for a `member:<name>` sub-row. */
  member?: string;
}

// ── Standup ────────────────────────────────────────────────────────────────

export interface MemberUpdate {
  name: string;
  summary: string;
  blockers: string;
  self_report: string;
  activity_count: number;
  links: [string, string][];
  practices: { rule: string; detail: string }[];
}

export interface StandupReportView {
  date: string;
  sprint_name: string;
  sprint_day: number;
  sprint_total_days: number;
  confidence_pct: number;
  confidence_label: string;
  confidence_rationale: string;
  team_summary: string;
  member_updates: MemberUpdate[];
  activity_counts: [string, number][];
  conflicts: { entity_id: string; summary: string }[];
  warnings: string[];
  my_name: string;
}

export interface StandupDashboard {
  session_id: string;
  session_name: string;
  my_name: string;
  cards: DashboardCard[];
  report: StandupReportView | null;
  config: Record<string, unknown> | null;
  schedule: { installed?: boolean; platform?: string; path?: string };
  review: { gaps: unknown[]; config_suggestions: unknown[] } | null;
  nudge: { missed_dates: string[] } | null;
  gap_issues: { issue_number?: number }[];
  /** Every run this session has done, newest first — the saved-runs hub. */
  history: RunSummary[];
  /** Non-zero when a past run is open instead of the latest. */
  run_id: number;
  /** Members with activity attributed today, by the rule both surfaces share. */
  active: string[];
}

export interface RunSummary {
  id: number;
  run_at: string;
  standup_date: string;
  sprint_day: number;
  confidence_pct: number;
  status: string;
}

export interface ScheduleView {
  session_id: string;
  enabled: boolean;
  time: string;
  lead_minutes: number;
  weekdays: string;
  delivery_channels: string[];
  remind_after: number;
  valid_channels: string[];
}

export function loadStandup(sessionId = '', runId = 0): Promise<StandupDashboard> {
  const query = new URLSearchParams();
  if (sessionId) query.set('session_id', sessionId);
  if (runId) query.set('run_id', String(runId));
  const suffix = query.toString();
  return apiGet<StandupDashboard>(`/api/standup/dashboard${suffix ? `?${suffix}` : ''}`);
}

/** Drop one run from the saved-runs hub. */
export function deleteRun(runId: number): Promise<{ deleted: boolean }> {
  return apiPost<{ deleted: boolean }>(`/api/standup/runs/${runId}/delete`);
}

export function loadSchedule(sessionId: string): Promise<ScheduleView> {
  return apiGet<ScheduleView>(`/api/standup/schedule?session_id=${encodeURIComponent(sessionId)}`);
}

export function saveSchedule(body: Partial<ScheduleView> & { session_id: string }): Promise<{ message: string }> {
  return apiPost<{ message: string }>('/api/standup/schedule', body);
}

export function runStandup(
  sessionId: string,
  deliver: boolean,
  onLine: (line: RunLine) => void,
): Promise<void> {
  return apiStream('/api/standup/run', { session_id: sessionId, deliver }, (line) => onLine(line as RunLine));
}

/** Record a thumbs up/down on one member's practice signal. */
export function ratePractice(sessionId: string, member: string, rule: string, verdict: string) {
  return callTool('standup_practice_feedback', { session_id: sessionId, member, rule, verdict });
}

// ── Analysis ───────────────────────────────────────────────────────────────

export interface AnalysisOptions {
  grid: { delivery: string[]; code: string[]; docs: string[] };
  features: { key: string; label: string }[];
  features_available: Record<string, boolean>;
  steps: string[];
  depths: string[];
  default_depth: string;
  window_presets: number[];
  default_window_days: number;
}

export interface ProfileSummary {
  team_id: string;
  source: string;
  project_key: string;
  team_name: string;
  analyzed_at: string;
  sample_sprints: number;
  sample_stories: number;
  velocity_avg: number;
}

export interface AnalysisResult {
  team_id: string;
  cards: DashboardCard[];
  profile: Record<string, unknown>;
  examples: Record<string, unknown>;
}

/** What a completed setup wizard asks the backend to run. */
export interface RunRequest {
  source: string;
  project_key?: string;
  features: string[] | null;
  components: Record<string, string[]>;
  members_map: Record<string, string[]> | null;
  analysis_scope: Record<string, string[]>;
  depth: string;
  window_days: number;
  model: string | null;
}

export function loadAnalysisOptions(): Promise<AnalysisOptions> {
  return apiGet<AnalysisOptions>('/api/analysis/options');
}

export function loadProfiles(): Promise<{ profiles: ProfileSummary[] }> {
  return apiGet<{ profiles: ProfileSummary[] }>('/api/analysis/profiles');
}

export function loadAnalysisResult(teamId: string): Promise<AnalysisResult> {
  return apiGet<AnalysisResult>(`/api/analysis/result/${encodeURIComponent(teamId)}`);
}

export function runAnalysis(body: RunRequest, onLine: (line: RunLine) => void): Promise<void> {
  return apiStream('/api/analysis/run', body, (line) => onLine(line as RunLine));
}

export function loadRoster(source: string, projectKey = '') {
  return callTool<{ members: { name: string }[] }>('team_roster', { source, project_key: projectKey });
}

// ── Runs ───────────────────────────────────────────────────────────────────

export type RunLine =
  | { type: 'op'; op_id: string }
  | { type: 'progress'; phase: string }
  | { type: 'run_id'; run_id: number }
  | { type: 'done'; report?: StandupReportView; result?: Record<string, unknown> }
  | { type: 'cancelled' }
  | { type: 'error'; message: string };

export interface RunState {
  opId: string;
  phases: string[];
  runId: number | null;
  report: StandupReportView | null;
  result: Record<string, unknown> | null;
  error: string;
  cancelled: boolean;
  finished: boolean;
}

export function emptyRun(): RunState {
  return {
    opId: '',
    phases: [],
    runId: null,
    report: null,
    result: null,
    error: '',
    cancelled: false,
    finished: false,
  };
}

/** Fold one NDJSON line into the run's state. Returns a new object. */
export function reduceRun(state: RunState, line: RunLine): RunState {
  switch (line.type) {
    case 'op':
      return { ...state, opId: line.op_id };
    case 'progress':
      return { ...state, phases: [...state.phases, line.phase] };
    case 'run_id':
      return { ...state, runId: line.run_id };
    case 'done':
      return {
        ...state,
        report: line.report ?? null,
        result: line.result ?? null,
        finished: true,
      };
    case 'cancelled':
      return { ...state, cancelled: true, finished: true };
    case 'error':
      return { ...state, error: line.message, finished: true };
    default:
      // An unknown line type is a newer backend, not a failure — ignore it.
      return state;
  }
}

export function cancelRun(opId: string): Promise<unknown> {
  return apiPost(`/api/ops/${encodeURIComponent(opId)}/cancel`);
}

// ── Setup steps ────────────────────────────────────────────────────────────

export interface StepPlan {
  /** The steps that apply to this selection, in order. */
  steps: string[];
  /** The component rows the selected features make selectable. */
  grid: AnalysisOptions['grid'];
  /** The payload these answers would run — the review step reads it. */
  run: RunRequest;
}

/**
 * Ask the backend which steps this partial selection still needs.
 *
 * Deliberately a round-trip rather than a rule mirrored in TypeScript: the
 * terminal and the desktop must ask the same wizard, and a second copy of the
 * rules is a second thing to drift. It runs when a checkbox changes, not per
 * keystroke.
 */
export function planSteps(answers: Record<string, unknown>): Promise<StepPlan> {
  return apiPost<StepPlan>('/api/analysis/steps', answers);
}

/**
 * The compact weekday form the scheduler stores: consecutive days collapse to
 * a range ("1-5"), gaps stay separate ("1,3,5"). Mirrors weekday_spec in
 * ceremonies/scheduler.py, which is what reads it back.
 */
export function weekdaySpec(days: number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  // An empty spec would install a job that never fires.
  if (!sorted.length) return '1-5';
  const parts: string[] = [];
  let start = sorted[0]!;
  let prior = start;
  for (const day of sorted.slice(1)) {
    if (day === prior + 1) {
      prior = day;
      continue;
    }
    parts.push(start === prior ? `${start}` : `${start}-${prior}`);
    start = day;
    prior = day;
  }
  parts.push(start === prior ? `${start}` : `${start}-${prior}`);
  return parts.join(',');
}
