// The wire and the arbiter behind the shell's ambience: the duck's voice, the
// music preferences, the beta gate, the consent modal and the feedback form.
//
// DuckVoice is a port of ui/shared/_duck_voice.py, ladder and all. It is ported
// rather than shared because what crosses the wire is the vocabulary (the quip
// table, served with the preferences) and not the arbitration — one is data and
// the other is thirty lines of clock logic that a bubble in a terminal and a
// bubble in a DOM need identical copies of.

import { apiGet, apiPost } from './api';

export interface MusicChannel {
  name: string;
  url: string;
}

export interface AmbienceState {
  duck: { enabled: boolean; quips: Record<string, string> };
  music: { channels: MusicChannel[]; channel: number; enabled: boolean };
  saver: { idle_seconds: number };
  pet: { enabled: boolean };
}

export interface BetaGate {
  headline: string;
  body: string[];
  seen: boolean;
}

export interface BetaGates {
  label: string;
  subtitle: string;
  footer: string;
  gates: Record<string, BetaGate>;
}

export interface ConsentRequest {
  req_id: string;
  path: string;
  mode: string;
  context: string;
}

export interface FeedbackOptions {
  types: string[];
  areas: string[];
  repo: string;
}

export interface FeedbackResult {
  ok: boolean;
  via: string;
  url: string;
  message: string;
}

export interface PolishResult {
  polished: { title: string; description: string } | null;
  status: string;
}

/** One ambient event off the SSE feed, forwarded by main. */
export interface AmbientEvent {
  type: string;
  [key: string]: unknown;
}

export const getAmbience = (): Promise<AmbienceState> => apiGet('/api/ambience');

export const setAmbience = (changes: Record<string, unknown>): Promise<AmbienceState> =>
  apiPost('/api/ambience', changes);

export const getBetaGates = (): Promise<BetaGates> => apiGet('/api/beta');

export const ackBetaGate = (modeKey: string): Promise<{ seen: boolean }> =>
  apiPost(`/api/beta/${encodeURIComponent(modeKey)}/ack`);

export const getConsentRequests = (): Promise<{ requests: ConsentRequest[]; choices: string[] }> =>
  apiGet('/api/consent');

export const resolveConsent = (reqId: string, choice: string): Promise<{ granted: boolean }> =>
  apiPost(`/api/consent/${encodeURIComponent(reqId)}`, { choice });

export const getFeedbackOptions = (): Promise<FeedbackOptions> => apiGet('/api/feedback/options');

export interface FeedbackDraft {
  kind: string;
  area: string;
  title: string;
  description: string;
}

export const submitFeedback = (draft: FeedbackDraft): Promise<FeedbackResult> => apiPost('/api/feedback', draft);

export const polishFeedback = (draft: FeedbackDraft): Promise<PolishResult> => apiPost('/api/feedback/polish', draft);

// ── the beta gate ────────────────────────────────────────────────────────────

/** Which gate guards which part of the app. Sub-routes are covered: opening a
 *  ship run is entering ship, and the gate is about the mode, not the page. */
const BETA_ROOTS: ReadonlyArray<readonly [string, string]> = [
  ['/humans/performance', 'performance'],
  ['/humans/ship', 'ship'],
  ['/agents/usage', 'agent-usage'],
  ['/agents/advisor', 'agent-advisor'],
  ['/agents/standup', 'agent-standup'],
  ['/agents/security', 'agent-security'],
];

/** The beta gate a route needs, or '' when it needs none. */
export function betaKeyFor(path: string): string {
  for (const [root, key] of BETA_ROOTS) {
    if (path === root || path.startsWith(`${root}/`)) return key;
  }
  return '';
}

// ── the duck's voice ─────────────────────────────────────────────────────────

export const PRIORITY_STICKY = 0; // a question waiting for an answer — never fades
export const PRIORITY_EVENT = 1;
export const PRIORITY_COACH = 2;

/** How long a line dwells before it fades. Coaching lingers a little longer. */
export const HOLD_MS = 2_600;
export const COACH_HOLD_MS = 4_000;

export interface DuckLine {
  text: string;
  priority: number;
  hold: number;
  seq: number;
  at: number;
}

/**
 * Decides what the duck says, one line at a time.
 *
 * Lower priority numbers win. A line still showing at a higher priority keeps
 * the bubble — a coaching nudge never interrupts a quip — and offering the text
 * already showing is a no-op rather than a restarted fade. There is deliberately
 * no ambient tier: rotating tips were tried in the terminal's bubble and read as
 * noise, so the duck speaks only when something actually happened.
 */
export class DuckVoice {
  private seq = 0;
  private line: DuckLine | null = null;
  muted = false;

  mute(muted: boolean): void {
    this.muted = muted;
    if (muted) this.line = null;
  }

  private expired(line: DuckLine, now: number): boolean {
    if (line.priority === PRIORITY_STICKY) return false; // waits for clearSticky, never the clock
    return now - line.at > line.hold;
  }

  /** Offer the duck a line. Returns whether he took the bubble. */
  say(text: string, priority = PRIORITY_EVENT, hold = HOLD_MS, now = Date.now()): boolean {
    if (this.muted || !text) return false;
    const live = this.line !== null && !this.expired(this.line, now);
    if (live && this.line && priority > this.line.priority) return false;
    if (live && this.line && this.line.text === text && this.line.priority === priority) return true;
    this.seq += 1;
    this.line = { text, priority, hold, seq: this.seq, at: now };
    return true;
  }

  /** A line that waits for an answer — full brightness until cleared. */
  saySticky(text: string, now = Date.now()): boolean {
    return this.say(text, PRIORITY_STICKY, Number.POSITIVE_INFINITY, now);
  }

  clearSticky(): void {
    if (this.line !== null && this.line.priority === PRIORITY_STICKY) this.line = null;
  }

  get sticky(): boolean {
    return this.line !== null && this.line.priority === PRIORITY_STICKY;
  }

  /** The line to draw this frame, or null. */
  tick(now = Date.now()): DuckLine | null {
    if (this.line === null || this.expired(this.line, now)) return null;
    return this.line;
  }
}

/** The app-wide voice. One window, one duck — the same rule as the TUI. */
let voice: DuckVoice | null = null;

export function duckVoice(): DuckVoice {
  if (voice === null) voice = new DuckVoice();
  return voice;
}

/** The quip table, as served with the ambience. Seeded so a page that finishes
 *  before the first read still has something to say. */
let quips: Record<string, string> = {};

export function loadQuips(table: Record<string, string>): void {
  quips = table;
}

/**
 * Say the line for a completion event, by key.
 *
 * This is how a page tells the duck something finished: it names what happened
 * and the vocabulary decides the words, so the tone is the same everywhere and
 * a page cannot invent its own. An unknown key says nothing, which is the right
 * failure — a missing quip is not worth a wrong one.
 */
export function quip(key: string): void {
  const line = quips[key];
  if (line) duckVoice().say(line);
}
