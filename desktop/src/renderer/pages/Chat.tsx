// The planning chat — the transcript, the stage rail, and one composer.
//
// A turn is an NDJSON stream: tokens animate a pending bubble, the finished
// reply replaces it, and `done` carries the new stage. The reducer that turns
// lines into bubbles lives in chat.ts so it can be tested without a DOM.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useRef, useState } from 'react';
import {
  type Bubble,
  type ChatLine,
  type QuestionView,
  STAGE_RAIL,
  type Stage,
  bubblesOf,
  cancelTurn,
  loadChat,
  reduceTurn,
  sendTurn,
  stageLabel,
} from '../chat';

const ARTIFACT_TITLES: Record<string, string> = {
  intake_summary: 'Your answers',
  prior_art: 'Prior art',
  analysis: 'Project analysis',
  epic: 'Project epic',
  features: 'Epics',
  stories: 'User stories',
  tasks: 'Tasks',
  sprints: 'Sprint plan',
  recap: 'The plan',
};

/** The chat's project id rides the hash query so the route path stays a literal. */
export function projectIdFromHash(hash: string): string {
  const query = hash.indexOf('?');
  if (query < 0) return '';
  return new URLSearchParams(hash.slice(query + 1)).get('id') ?? '';
}

export function Chat() {
  // The router keys on the path alone, so switching conversations changes only
  // the query — this component has to watch the hash itself to notice.
  const [projectId, setProjectId] = useState(() => projectIdFromHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setProjectId(projectIdFromHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [question, setQuestion] = useState<QuestionView | null>(null);
  const [stage, setStage] = useState<Stage>('intake');
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState('');
  const [busy, setBusy] = useState(false);
  const [opId, setOpId] = useState('');
  const [error, setError] = useState('');
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!projectId) {
      setError('No conversation was named — start one from Planning.');
      return;
    }
    loadChat(projectId).then(
      (view) => {
        setBubbles(bubblesOf(view.transcript));
        setQuestion(view.question);
        setStage(view.stage);
      },
      (e: Error) => setError(e.message),
    );
  }, [projectId]);

  useEffect(() => {
    foot.current?.scrollIntoView({ block: 'end' });
  }, [bubbles, pending]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError('');
    setDraft('');
    setBubbles((prior) => [...prior, { role: 'user', text }]);
    const lines: ChatLine[] = [];
    let streamed = '';
    try {
      await sendTurn(projectId, text, (line) => {
        lines.push(line);
        if (line.type === 'op') setOpId(line.op_id);
        else if (line.type === 'token') {
          streamed += line.text;
          setPending(streamed);
        }
      });
    } catch (e) {
      setError((e as Error).message);
    }
    const turn = reduceTurn(lines);
    setPending('');
    setOpId('');
    setBusy(false);
    if (turn.error) setError(turn.error);
    if (turn.cancelled) setError('Cancelled — nothing was changed.');
    if (turn.bubbles.length) setBubbles((prior) => [...prior, ...turn.bubbles]);
    if (turn.stage) setStage(turn.stage);
    // The question view (choices, progress, the phase label) is derived from
    // the state the turn just produced, so it is re-read rather than guessed.
    loadChat(projectId).then((view) => setQuestion(view.question), () => undefined);
  }

  const choices = !busy && question?.choices ? question.choices : null;

  return (
    <div class="chat">
      <header class="chat-head">
        <div class="stage-rail">
          {STAGE_RAIL.map((step) => (
            <span key={step.stage} class={step.stage === stage ? 'stage active' : 'stage'}>
              {step.label}
            </span>
          ))}
        </div>
        <div class="chat-sub">
          {question?.progress || stageLabel(stage)}
          {question?.phase_label ? ` · ${question.phase_label}` : ''}
        </div>
      </header>

      <div class="chat-scroll">
        {bubbles.map((bubble, index) => (
          <Row key={`${index}-${bubble.role}-${bubble.kind ?? ''}`} bubble={bubble} />
        ))}
        {pending && (
          <div class="bubble assistant streaming">
            <p>{pending}</p>
          </div>
        )}
        {busy && !pending && (
          <div class="bubble assistant working">
            <Duck state="idle" size={22} /> <span>Thinking…</span>
          </div>
        )}
        <div ref={foot} />
      </div>

      {error && <p class="chat-error">{error}</p>}

      {choices && (
        <div class="chat-choices">
          {choices.map(([label], index) => (
            <button key={label} type="button" disabled={busy} onClick={() => void send(label)}>
              <span class="choice-key">{index + 1}</span>
              {label}
            </button>
          ))}
        </div>
      )}

      <div class="composer">
        <textarea
          rows={3}
          placeholder={busy ? 'Working — your message sends when this finishes…' : 'Message yeaboi…'}
          value={draft}
          onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }}
        />
        <div class="composer-actions">
          <span class="composer-hint">Enter sends · Shift+Enter for a new line</span>
          {busy && opId ? (
            <button type="button" onClick={() => void cancelTurn(opId)}>
              Stop
            </button>
          ) : (
            <button type="button" class="primary" disabled={!draft.trim()} onClick={() => void send(draft)}>
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ bubble }: { bubble: Bubble }) {
  if (bubble.role === 'card') {
    return (
      <div class="bubble card">
        <strong>{ARTIFACT_TITLES[bubble.kind ?? ''] ?? bubble.kind}</strong>
        <span class="card-note">Open the plan to read this in full.</span>
      </div>
    );
  }
  return (
    <div class={`bubble ${bubble.role}`}>
      <p>{bubble.text}</p>
    </div>
  );
}
