// The live boards' and shared artifacts' wire — the shapes
// contracts/v1/app_http.md pins, the calls that produce them, and the pure
// helpers over them (plain functions, so vitest can drive them without a DOM).
//
// No wizard rules and no card vocabulary are re-declared here: the poker setup
// steps come from /api/poker/options, exactly as the analysis stepper asks
// /api/analysis/steps, because the terminal and the desktop must walk the same
// wizard and a second copy of the rules is a second thing to drift.

import { apiGet, apiPost, apiStream, callTool } from './api';

// ── Boards ─────────────────────────────────────────────────────────────────

export interface LinkState {
  /** `idle` | `starting` | `ready` | `failed` | `off` (YEABOI_NO_TUNNEL). */
  state: string;
  status: string;
  url: string;
  failed: boolean;
  expired: boolean;
  starting: boolean;
  /** Time-critical only — rendered above the page's own status text. */
  notice: string;
}

export interface RetroCard {
  id: string;
  text: string;
  author: string;
  votes?: number;
}

export interface BoardSnapshot {
  board_id: string;
  /** `retro` or `poker`. */
  kind: string;
  title: string;
  session_id: string;
  project_name: string;
  started_at: string;
  /** Private: carries the admin token. Opens the board window, never an invite. */
  host_url: string;
  share_url: string;
  display_code: string;
  link: LinkState;
  state: Record<string, unknown>;
}

export interface RetroBoardState {
  grids: Record<string, RetroCard[]>;
  carried: { text: string }[];
}

export function loadBoards(): Promise<{ boards: BoardSnapshot[] }> {
  return apiGet<{ boards: BoardSnapshot[] }>('/api/boards');
}

export function loadBoard(boardId: string): Promise<BoardSnapshot> {
  return apiGet<BoardSnapshot>(`/api/boards/${encodeURIComponent(boardId)}`);
}

export function startRetroBoard(): Promise<BoardSnapshot> {
  return apiPost<BoardSnapshot>('/api/boards/retro');
}

export function startPokerBoard(body: {
  source: string;
  scope_label: string;
  tickets: unknown[];
}): Promise<BoardSnapshot> {
  return apiPost<BoardSnapshot>('/api/boards/poker', body);
}

export function retryLink(boardId: string): Promise<{ link: LinkState }> {
  return apiPost<{ link: LinkState }>(`/api/boards/${encodeURIComponent(boardId)}/link`);
}

export function boardInvite(boardId: string): Promise<{ invite: string; display_code: string }> {
  return apiGet<{ invite: string; display_code: string }>(`/api/boards/${encodeURIComponent(boardId)}/invite`);
}

export function generateActionItems(boardId: string): Promise<{ message: string; state: RetroBoardState }> {
  return apiPost<{ message: string; state: RetroBoardState }>(`/api/boards/${encodeURIComponent(boardId)}/actions`);
}

export function closeBoard(boardId: string): Promise<{ closed: boolean; run_id: number }> {
  return apiPost<{ closed: boolean; run_id: number }>(`/api/boards/${encodeURIComponent(boardId)}/close`);
}

export interface RetroRun {
  id: number;
  retro_date: string;
  run_at: string;
  project_name?: string;
  sprint_name?: string;
  card_count?: number;
  action_count?: number;
}

export interface PokerRun {
  id: number;
  poker_date: string;
  run_at: string;
  /** Poker rows carry their own session; retro rows do not — see below. */
  session_id: string;
  project_name?: string;
  scope_label?: string;
  source?: string;
  ticket_count?: number;
  estimated_count?: number;
}

/**
 * Past retros, for the hub.
 *
 * The rows land under `history`, and the session they belong to is a *sibling*
 * of it rather than a column on each row — so an artifact reference for one of
 * these takes its session from the envelope and its run from the row.
 */
export function retroHistory(limit = 30) {
  return callTool<{ history: RetroRun[]; session_id: string }>('retro_history', { limit });
}

export function pokerHistory(limit = 30) {
  return callTool<{ history: PokerRun[] }>('poker_history', { limit });
}

// ── Poker setup ────────────────────────────────────────────────────────────

export interface PickOption {
  key: string;
  label: string;
  sub: string;
  checked?: boolean;
}

export interface PokerOptions {
  steps: string[];
  titles: Record<string, string>;
  sources: PickOption[];
  source_hint: string;
  scopes: PickOption[];
}

export function loadPokerOptions(): Promise<PokerOptions> {
  return apiGet<PokerOptions>('/api/poker/options');
}

export function loadPokerSprints(source: string): Promise<{
  sprints: Record<string, unknown>[];
  options: PickOption[];
  default_index: number;
}> {
  return apiGet(`/api/poker/sprints?source=${encodeURIComponent(source)}`);
}

export function loadPokerTypes(source: string): Promise<{ types: PickOption[]; hint: string }> {
  return apiGet(`/api/poker/types?source=${encodeURIComponent(source)}`);
}

export function fetchPokerTickets(body: {
  source: string;
  scope?: string;
  sprint?: Record<string, unknown> | null;
  include_types?: string[] | null;
}): Promise<{ tickets: unknown[]; scope_label: string; source: string; message: string }> {
  return apiPost('/api/poker/tickets', body);
}

// ── Export / share / anonymize ─────────────────────────────────────────────

/** What addresses one stored artifact. `kind` is standup | retro | analysis. */
export interface ArtifactRef {
  kind: string;
  session_id?: string;
  run_id?: number;
}

export interface Destination {
  key: string;
  label: string;
  description: string;
  /** Non-empty when the destination is configured but cannot publish yet. */
  blocked: string;
  /** True when the client completes it (the clipboard), not the backend. */
  local: boolean;
}

export interface ExportResult {
  destination: string;
  ok?: boolean;
  message?: string;
  url?: string;
  title?: string;
  markdown?: string;
  paths?: Record<string, string>;
}

export function loadDestinations(mode: string, extras: string[] = []): Promise<{ destinations: Destination[] }> {
  const query = new URLSearchParams({ mode });
  if (extras.length) query.set('extras', extras.join(','));
  return apiGet<{ destinations: Destination[] }>(`/api/export/destinations?${query}`);
}

export function exportArtifact(ref: ArtifactRef, destination: string): Promise<ExportResult> {
  return apiPost<ExportResult>('/api/export', { ...ref, destination });
}

export interface ShareSnapshot {
  share_id: string;
  kind: string;
  title: string;
  session_id: string;
  run_id: number;
  started_at: string;
  share_url: string;
  display_code: string;
  editable: boolean;
  /** Corrections recorded in THIS session — a delta, never the total. */
  edits: number;
  editors: string[];
  link: LinkState;
}

export function loadShares(): Promise<{ shares: ShareSnapshot[] }> {
  return apiGet<{ shares: ShareSnapshot[] }>('/api/shares');
}

export function loadShare(shareId: string): Promise<ShareSnapshot> {
  return apiGet<ShareSnapshot>(`/api/shares/${encodeURIComponent(shareId)}`);
}

export function startShare(ref: ArtifactRef, editable = true): Promise<ShareSnapshot> {
  return apiPost<ShareSnapshot>('/api/shares', { ...ref, editable });
}

export function shareInvite(shareId: string): Promise<{ invite: string; display_code: string }> {
  return apiGet(`/api/shares/${encodeURIComponent(shareId)}/invite`);
}

export function discardShareEdits(
  shareId: string,
): Promise<{ dropped: number; message: string; share: ShareSnapshot }> {
  return apiPost(`/api/shares/${encodeURIComponent(shareId)}/discard`);
}

/**
 * Stop sharing. `commit` defaults to false everywhere for the same reason:
 * keeping somebody else's corrections is the host's decision, never a
 * consequence of closing a window.
 */
export function closeShare(
  shareId: string,
  commit = false,
): Promise<{ recorded: number; committed_run_id: number; message: string }> {
  return apiPost(`/api/shares/${encodeURIComponent(shareId)}/close`, { commit });
}

export interface KindCapability {
  kind: string;
  export: boolean;
  share: boolean;
  anonymize: boolean;
  edit: boolean;
}

/**
 * What each artifact kind can do.
 *
 * Read rather than mirrored: a surface that kept its own table would be the one
 * offering an Export button that always refuses. Poker exports and nothing
 * else; a team profile shares read-only; only a standup or a retro is
 * correctable.
 */
export function loadKindCapabilities(): Promise<{ kinds: KindCapability[] }> {
  return apiGet<{ kinds: KindCapability[] }>('/api/artifacts/kinds');
}

export interface ArtifactFieldSpec {
  path: string;
  kind: string;
  label: string;
  max_length: number;
  max_items: number;
}

export interface ArtifactEdits {
  kind: string;
  ops: string[];
  artifact: { label: string; note: string; fields: ArtifactFieldSpec[]; headless: boolean; shared: boolean };
  count: number;
  editors: string[];
  /** Always "self-declared": whoever held the link typed the name. */
  attribution: string;
  edits: { id: string; seq: number; op: string; path: string; value: string; author: string; at: string }[];
}

export function loadArtifactEdits(ref: ArtifactRef): Promise<ArtifactEdits> {
  const query = new URLSearchParams();
  if (ref.session_id) query.set('session_id', ref.session_id);
  if (ref.run_id) query.set('run_id', String(ref.run_id));
  return apiGet<ArtifactEdits>(`/api/artifacts/${encodeURIComponent(ref.kind)}/edits?${query}`);
}

export function applyArtifactEdits(ref: ArtifactRef, edits: object[], author: string) {
  return callTool('artifact_edit_apply', { ...ref, edits, author });
}

// ── Anonymize ──────────────────────────────────────────────────────────────

export type AnonLine =
  | { type: 'op'; op_id: string }
  | { type: 'progress'; phase: string }
  | { type: 'done'; note: string; replacements: [string, string][]; warnings: string[] }
  | { type: 'error'; message: string };

export interface AnonState {
  opId: string;
  phases: string[];
  note: string;
  replacements: [string, string][];
  warnings: string[];
  error: string;
  finished: boolean;
}

export function emptyAnon(): AnonState {
  return { opId: '', phases: [], note: '', replacements: [], warnings: [], error: '', finished: false };
}

/** Fold one NDJSON line into the anonymize pass's state. Returns a new object. */
export function reduceAnon(state: AnonState, line: AnonLine): AnonState {
  switch (line.type) {
    case 'op':
      return { ...state, opId: line.op_id };
    case 'progress':
      return { ...state, phases: [...state.phases, line.phase] };
    case 'done':
      return {
        ...state,
        note: line.note,
        replacements: line.replacements,
        warnings: line.warnings,
        finished: true,
      };
    case 'error':
      return { ...state, error: line.message, finished: true };
    default:
      // An unknown line type is a newer backend, not a failure — ignore it.
      return state;
  }
}

export function anonymizeArtifact(
  ref: ArtifactRef,
  instruction: string,
  onLine: (line: AnonLine) => void,
): Promise<void> {
  return apiStream('/api/anonymize', { ...ref, instruction }, (line) => onLine(line as AnonLine));
}

/**
 * Apply a replacement map to one string, longest original first so
 * "Acme Payments" is masked before the substring "Acme".
 *
 * The renderer masks what it is already showing rather than fetching a second,
 * masked copy — the same rule the terminal follows, and the reason a mask can
 * be reverted without another round-trip. Mirrors apply_replacements in
 * anonymize/apply.py, minus the word-boundary regex: this masks values the page
 * is rendering, where a plain replace is what the eye expects.
 */
export function maskText(text: string, replacements: [string, string][]): string {
  if (!text || !replacements.length) return text;
  const ordered = [...replacements].sort((a, b) => b[0].length - a[0].length);
  let masked = text;
  for (const [original, placeholder] of ordered) {
    if (!original) continue;
    masked = masked.split(original).join(placeholder);
  }
  return masked;
}

// ── Board windows ──────────────────────────────────────────────────────────

/**
 * Open a board in its own top-level window.
 *
 * A board page sends `X-Frame-Options: DENY`, so it can never be an iframe —
 * and the host link carries the admin token, so main opens it rather than the
 * renderer navigating to it.
 */
export function openBoardWindow(boardId: string): Promise<unknown> {
  const bridge = (window as unknown as { yeaboi?: { openBoard?: (id: string) => Promise<unknown> } }).yeaboi;
  if (!bridge?.openBoard) return Promise.reject(new Error('board windows need the desktop shell'));
  return bridge.openBoard(boardId);
}
