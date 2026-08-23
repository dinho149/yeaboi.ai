// The planning chat's wire — the shapes contracts/v1/app_http.md pins, plus
// the calls that produce them. The reducer over a turn's events lives here
// too (a plain function, so the vitest suite can drive it without a DOM).

import { apiGet, apiPost, apiStream } from './api';

/** The stages the conversation can be parked on — the one predicate every surface routes on. */
export type Stage = 'intake' | 'review' | 'pipeline' | 'epic' | 'capacity' | 'spike' | 'chat';

export interface QuestionView {
  question_text: string;
  preamble_lines: string[];
  choices: [string, boolean][] | null;
  multi_select: boolean;
  auto_submit: boolean;
  prior_art: boolean;
  suggestion: string | null;
  progress: string;
  phase_label: string;
  current_question: number;
}

export type ChatLine =
  | { type: 'op'; op_id: string }
  | { type: 'token'; text: string }
  | { type: 'assistant'; text: string }
  | { type: 'user'; text: string }
  | { type: 'question'; text: string; number: number }
  | { type: 'await_confirm'; kind: string; prompt: string }
  | { type: 'artifact'; kind: string }
  | { type: 'done'; stage: Stage }
  | { type: 'cancelled' }
  | { type: 'error'; message: string };

export interface SessionView {
  project_id: string;
  stage: Stage;
  transcript: ChatLine[];
  question: QuestionView;
  /** The description, until it has been sent as the conversation's first turn. */
  opening: string;
}

/** One drawn row of the conversation. Cards carry a kind, prose carries text. */
export interface Bubble {
  role: 'user' | 'assistant' | 'card';
  text: string;
  kind?: string;
}

export function createChat(description: string, intakeMode = ''): Promise<SessionView> {
  return apiPost<SessionView>('/api/chat/sessions', {
    description,
    ...(intakeMode ? { intake_mode: intakeMode } : {}),
  });
}

export function loadChat(projectId: string): Promise<SessionView> {
  return apiGet<SessionView>(`/api/chat/sessions/${encodeURIComponent(projectId)}`);
}

export function sendTurn(projectId: string, text: string, onLine: (line: ChatLine) => void): Promise<void> {
  return apiStream(`/api/chat/sessions/${encodeURIComponent(projectId)}/send`, { text }, (line) =>
    onLine(line as ChatLine),
  );
}

export function cancelTurn(opId: string): Promise<unknown> {
  return apiPost(`/api/ops/${encodeURIComponent(opId)}/cancel`);
}

/** The transcript a session view draws as, ignoring line types a card owns. */
export function bubblesOf(lines: ChatLine[]): Bubble[] {
  const bubbles: Bubble[] = [];
  for (const line of lines) {
    if (line.type === 'user') bubbles.push({ role: 'user', text: line.text });
    else if (line.type === 'assistant' || line.type === 'question')
      bubbles.push({ role: 'assistant', text: line.text });
    else if (line.type === 'artifact') bubbles.push({ role: 'card', text: '', kind: line.kind });
    else if (line.type === 'await_confirm') {
      bubbles.push({ role: 'card', text: '', kind: line.kind });
      bubbles.push({ role: 'assistant', text: line.prompt });
    }
  }
  return bubbles;
}

/** What a finished turn leaves behind: new bubbles, the new stage, how it ended. */
export interface TurnResult {
  bubbles: Bubble[];
  stage: Stage | null;
  opId: string;
  error: string;
  cancelled: boolean;
}

export function reduceTurn(lines: ChatLine[]): TurnResult {
  const result: TurnResult = { bubbles: [], stage: null, opId: '', error: '', cancelled: false };
  for (const line of lines) {
    if (line.type === 'op') result.opId = line.op_id;
    else if (line.type === 'done') result.stage = line.stage;
    else if (line.type === 'cancelled') result.cancelled = true;
    else if (line.type === 'error') result.error = line.message;
  }
  // Tokens are the same reply the assistant/question line carries in full, so
  // they animate the pending bubble and never become one of their own.
  result.bubbles = bubblesOf(lines.filter((line) => line.type !== 'token'));
  return result;
}

/** The stage rail — the pipeline as the chat walks it, with the live one marked. */
export const STAGE_RAIL: { stage: Stage; label: string }[] = [
  { stage: 'intake', label: 'Describe' },
  { stage: 'epic', label: 'Epic' },
  { stage: 'pipeline', label: 'Build' },
  { stage: 'review', label: 'Review' },
  { stage: 'chat', label: 'Refine' },
];

export function stageLabel(stage: Stage): string {
  if (stage === 'capacity') return 'Capacity';
  if (stage === 'spike') return 'Architecture spike';
  return STAGE_RAIL.find((step) => step.stage === stage)?.label ?? stage;
}
