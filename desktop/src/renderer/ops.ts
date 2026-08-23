// The ceremonies, Slack, Agents and provenance wire — the shapes
// contracts/v1/app_http.md pins, the calls that produce them, and the pure
// reducer over an Agents run's NDJSON lines.
//
// No rule is re-declared here. Which ceremonies drifted, what a paused one
// says instead of a cadence, whether the Slack lane can read back and how
// fresh a saved agent report is are all backend answers.

import { type Envelope, apiGet, apiPost, apiStream, callTool } from './api';

// ── Ceremonies ─────────────────────────────────────────────────────────────

export interface CeremonyRun {
  ceremony: string;
  fired_at: string;
  outcome: string;
  scheduled: boolean;
  cost_usd: number;
  delivery: [string, boolean][];
  detail: string;
  error: string;
}

export interface CeremonyRow {
  name: string;
  mode: string;
  at: string;
  weekdays: string;
  channels: string[];
  enabled: boolean;
  monthly_cap_usd: number;
  /** "Mon–Fri at 09:00" — the backend's phrasing, never rebuilt here. */
  cadence: string;
  /** The same, unless it is paused or skipping — then it says that instead. */
  next_fire: string;
  last_run: CeremonyRun | null;
  month_spend_usd: number;
}

export interface CeremonyParam {
  name: string;
  kind: string;
  default: string;
  label: string;
  help: string;
}

export interface CeremonyModeOption {
  key: string;
  label: string;
  blurb: string;
  est_cost_usd: number;
  default_at: string;
  default_weekdays: string;
  params: CeremonyParam[];
}

export interface CeremoniesPage {
  session_id: string;
  ceremonies: CeremonyRow[];
  /** Where the store and the operating system disagree. */
  drift: string[];
  modes: CeremonyModeOption[];
  channels: string[];
  add_hint: string;
  empty_message: string;
}

export interface DeclaredCeremony {
  ceremony: CeremonyRow;
  cadence: string;
  scheduler: string;
  /** The equivalent terminal command, so the page can say what it installed. */
  command: string;
}

export const loadCeremonies = (): Promise<CeremoniesPage> => apiGet('/api/ceremonies');

export const declareCeremony = (body: Record<string, unknown>): Promise<DeclaredCeremony> =>
  apiPost('/api/ceremonies', body);

export const setCeremonyEnabled = (name: string, enabled: boolean): Promise<{ scheduler: string }> =>
  apiPost(`/api/ceremonies/${encodeURIComponent(name)}/enabled`, { enabled });

export const removeCeremony = (name: string): Promise<{ removed: boolean; scheduler: string }> =>
  apiPost(`/api/ceremonies/${encodeURIComponent(name)}/remove`, {});

export const runCeremony = (name: string, onLine: (line: unknown) => void): Promise<void> =>
  apiStream(`/api/ceremonies/${encodeURIComponent(name)}/run`, {}, onLine);

export const ceremonyHistory = (name = ''): Promise<Envelope<{ runs: CeremonyRun[] }>> =>
  callTool('ceremonies_history', name ? { ceremony: name } : {});

// ── The inbound Slack lane ─────────────────────────────────────────────────

export interface SlackIdentity {
  slack_user: string;
  member: string;
  [field: string]: unknown;
}

export interface SlackPage {
  session_id: string;
  two_way: boolean;
  /** Why the lane cannot read back, when it cannot. */
  why: string;
  identities: SlackIdentity[];
  linked: number;
  interval_min: number;
  link_hint: string;
  empty_message: string;
  events: Record<string, unknown>[];
  recent_polls: Record<string, unknown>[];
}

export const loadSlack = (): Promise<SlackPage> => apiGet('/api/slack');

export const linkSlackMember = (
  slackUser: string,
  member: string,
  unlink = false,
): Promise<{ identities: SlackIdentity[] }> =>
  apiPost('/api/slack/link', { slack_user: slackUser, member, unlink });

export const pollSlack = (): Promise<{
  outcome: string;
  declined: boolean;
  events_applied: number;
  events_seen: number;
}> => apiPost('/api/slack/poll', {});

// ── The Agents family ──────────────────────────────────────────────────────

export interface AgentModeOption {
  key: string;
  kind: string;
  label: string;
  blurb: string;
  last_report_at: string;
}

export interface AgentModes {
  modes: AgentModeOption[];
  actions: string[];
  beta_notice: string;
}

export interface AgentLatest {
  kind: string;
  label: string;
  report: Record<string, unknown> | null;
  as_of: string;
}

export interface AgentComponent {
  component_id: string;
  label: string;
  status: string;
  current?: number;
  total?: number;
  detail?: string;
}

export interface AgentRunState {
  /** Latest event per phase, in first-seen order — the phase checklist. */
  components: AgentComponent[];
  /** The bare-string steps, for the modes whose engines emit those. */
  phases: string[];
  report: Record<string, unknown> | null;
  error: string;
  finished: boolean;
}

export function emptyAgentRun(): AgentRunState {
  return { components: [], phases: [], report: null, error: '', finished: false };
}

/** Fold one NDJSON line into the run's state. Returns a new object. */
export function reduceAgentRun(state: AgentRunState, line: unknown): AgentRunState {
  const row = (line ?? {}) as Record<string, unknown>;
  switch (row.type) {
    case 'component': {
      const event = row.component as AgentComponent;
      // Latest-per-phase, first-seen order: a scan emits an event per file and
      // appending them all would draw the same phase hundreds of times.
      const known = state.components.some((c) => c.component_id === event.component_id);
      const components = known
        ? state.components.map((c) => (c.component_id === event.component_id ? event : c))
        : [...state.components, event];
      return { ...state, components };
    }
    case 'progress':
      return { ...state, phases: [...state.phases, String(row.phase ?? '')] };
    case 'done':
      return { ...state, report: (row.report ?? null) as Record<string, unknown> | null, finished: true };
    case 'error':
      return { ...state, error: String(row.message ?? 'The pass stopped.'), finished: true };
    default:
      // An unknown line type is a newer backend, not a failure — ignore it.
      return state;
  }
}

export const loadAgentModes = (): Promise<AgentModes> => apiGet('/api/agents/modes');

export const loadAgentLatest = (kind: string): Promise<AgentLatest> =>
  apiGet(`/api/agents/${encodeURIComponent(kind)}/latest`);

export const runAgentMode = (kind: string, onLine: (line: unknown) => void): Promise<void> =>
  apiStream(`/api/agents/${encodeURIComponent(kind)}/run`, {}, onLine);

export const exportAgentReport = (
  kind: string,
  destination: 'files' | 'copy',
): Promise<{ message?: string; markdown?: string }> =>
  apiPost(`/api/agents/${encodeURIComponent(kind)}/export`, { destination });

/** The agents mode a `#/agents/<kind>` hash addresses. */
export function agentKindFromHash(hash: string): string {
  const path = hash.slice(1).split('?')[0] ?? '';
  return path.startsWith('/agents/') ? path.slice('/agents/'.length) : '';
}

// ── Provenance ─────────────────────────────────────────────────────────────
//
// Both reads are MCP tools with no progress, no cancel and no page-shaped gap,
// so they go through the dispatcher rather than getting native routes.

export interface DecisionRow {
  entity_id: string;
  entity_type: string;
  record_kind: string;
  agent_id: string;
  role: string;
  timestamp: string;
  detail: string;
  inputs: string[];
  sequence_id: number;
}

export interface ProvenanceAudit {
  generated_at: string;
  window_days: number;
  chain_valid: boolean;
  total_records: number;
  window_records: number;
  records_by_type: [string, number][];
  recent: DecisionRow[];
  breaks: [number, string, string][];
  warnings: string[];
}

export interface ProvenanceTrace {
  entity_id: string;
  found: boolean;
  records: DecisionRow[];
  warnings: string[];
}

export const provenanceAudit = (windowDays: number): Promise<Envelope<ProvenanceAudit>> =>
  callTool('provenance_audit', { window_days: windowDays });

export const provenanceTrace = (entityId: string, depth = 2): Promise<Envelope<ProvenanceTrace>> =>
  callTool('provenance_trace', { entity_id: entityId, depth });
