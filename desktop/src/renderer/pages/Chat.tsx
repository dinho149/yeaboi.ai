// The planning chat — the transcript, the stage rail, and one composer.
//
// A turn is an NDJSON stream: tokens animate a pending bubble, the finished
// reply replaces it, and `done` carries the new stage. The reducer that turns
// lines into bubbles lives in chat.ts so it can be tested without a DOM.
//
// Slash commands never reach the model: they are parsed here and either become
// a local action or a literal the intake node consumes. That is the terminal's
// security invariant, and it holds here for the same reason — there is nothing
// to guard when no text is sent.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useRef, useState } from 'react';
import { MicButton } from '../components/MicButton';
import { QuestionsPanel } from '../components/QuestionsPanel';
import { matchingCommands, parseCommand, unknownCommandNotice } from '../commands';
import { openShortcuts } from '../palette';
import { appendSpoken, toBase64 } from '../voice';
import { getAmbience, setAmbience } from '../ambience';
import {
  type Bubble,
  type ChatLine,
  type QuestionView,
  STAGE_RAIL,
  type Stage,
  attachImage,
  bubblesOf,
  cancelTurn,
  loadChat,
  reduceTurn,
  sendTurn,
  stageLabel,
  switchSize,
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
  // A dim local line — what the terminal calls a note. Never a graph turn.
  const [notice, setNotice] = useState('');
  const [questions, setQuestions] = useState(false);
  // Paths in the order they were pasted; their `[image #N]` chips name them.
  const [attachments, setAttachments] = useState<string[]>([]);
  const [duckOn, setDuckOn] = useState(true);
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
        // A conversation opened from Planning still owes its first turn: the
        // description has to reach the graph as messages[0] or the intake has
        // nothing to plan.
        if (view.opening) void send(view.opening);
      },
      (e: Error) => setError(e.message),
    );
  }, [projectId]);

  useEffect(() => {
    foot.current?.scrollIntoView({ block: 'end' });
  }, [bubbles, pending]);

  useEffect(() => {
    getAmbience().then((state) => setDuckOn(state.duck.enabled), () => undefined);
  }, []);

  /**
   * A screenshot pasted into the box.
   *
   * The image is kept backend-side and what lands in the text is its chip, so
   * deleting the chip detaches the image — the terminal's rule, and the reason
   * the whole attachment list travels with every turn.
   */
  async function paste(event: ClipboardEvent) {
    const file = Array.from(event.clipboardData?.items ?? [])
      .find((item) => item.kind === 'file' && item.type.startsWith('image/'))
      ?.getAsFile();
    if (!file) return; // ordinary text — let the browser paste it
    event.preventDefault();
    setNotice('Pasting image…');
    try {
      const encoded = await toBase64(file);
      const { path, chip } = await attachImage(projectId, encoded, file.type, attachments.length + 1);
      setAttachments((prior) => [...prior, path]);
      setDraft((prior) => (prior ? `${prior} ${chip}` : chip));
      setNotice('');
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  /** A composer submission: a command runs locally, anything else is a turn. */
  async function submit(text: string) {
    const intent = parseCommand(text);
    if (!intent) return send(text);
    setDraft('');
    setNotice('');
    switch (intent.kind) {
      case 'shortcuts':
        return openShortcuts();
      case 'export':
        window.location.hash = `#/humans/planning/plan?id=${encodeURIComponent(projectId)}`;
        return;
      case 'questions':
        setQuestions(true);
        return;
      case 'summary':
        // The card the terminal pushes: the answers so far, opened in full
        // from the plan rather than reprinted into the transcript.
        setBubbles((prior) => [...prior, { role: 'card', text: '', kind: 'intake_summary' }]);
        return;
      case 'duck':
        return duck();
      case 'size':
        return size(intent.mode);
      case 'send':
        return send(intent.text);
      default:
        setNotice(unknownCommandNotice(intent.name));
    }
  }

  async function duck() {
    try {
      const state = await setAmbience({ duck_enabled: !duckOn });
      setDuckOn(state.duck.enabled);
      setNotice(state.duck.enabled ? 'The duck has its voice back.' : 'Duck muted.');
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  async function size(mode: 'small_project' | 'smart') {
    const label = mode === 'small_project' ? 'Small' : 'Large';
    try {
      const result = await switchSize(projectId, mode);
      if (!result.changed) {
        setNotice(`Already planning ${label}.`);
        return;
      }
      setNotice(`Switched to ${label} — I kept all your answers.`);
      // The switch reopens the intake for the new mode, so one empty turn
      // gets the first question for it — the terminal's re-entry, unchanged.
      if (result.reopened) await send('', { synthetic: true });
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  /** `synthetic` sends an empty turn — the graph re-entry a size switch needs. */
  async function send(text: string, { synthetic = false }: { synthetic?: boolean } = {}) {
    if (busy) return;
    if (!text.trim() && !attachments.length && !synthetic) return;
    setBusy(true);
    setError('');
    setNotice('');
    setDraft('');
    const sent = attachments;
    setAttachments([]);
    if (text) setBubbles((prior) => [...prior, { role: 'user', text }]);
    const lines: ChatLine[] = [];
    let streamed = '';
    try {
      await sendTurn(
        projectId,
        text,
        (line) => {
          lines.push(line);
          if (line.type === 'op') setOpId(line.op_id);
          else if (line.type === 'token') {
            streamed += line.text;
            setPending(streamed);
          }
        },
        sent,
      );
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
  // The /-menu, on the same rule the terminal uses: a leading slash on the
  // first line. A slash anywhere else is prose ("http://…", "and/or").
  const menu = busy || draft.includes('\n') ? [] : matchingCommands(draft);

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
          <Row key={`${index}-${bubble.role}-${bubble.kind ?? ''}`} bubble={bubble} projectId={projectId} />
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
      {notice && <p class="chat-notice">{notice}</p>}

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
        {menu.length > 0 && (
          <ul class="slash-menu">
            {menu.map((command, index) => (
              <li key={command.name}>
                <button
                  type="button"
                  class={index === 0 ? 'slash-row first' : 'slash-row'}
                  onClick={() => void submit(`/${command.name}`)}
                >
                  <span class="slash-name">/{command.name}</span>
                  <span class="slash-help">{command.help}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          rows={3}
          placeholder={busy ? 'Working — your message sends when this finishes…' : 'Message yeaboi… (/ for commands)'}
          value={draft}
          onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
          onPaste={(e) => void paste(e as unknown as ClipboardEvent)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              // A half-typed verb completes rather than submitting: the menu is
              // showing exactly one command, and that is what was meant.
              const only = menu.length === 1 && draft.trim() !== `/${menu[0]?.name}` ? menu[0] : null;
              if (only) setDraft(`/${only.name}`);
              else void submit(draft);
            } else if (e.key === 'Escape' && menu.length) {
              e.preventDefault();
              setDraft('');
            }
          }}
        />
        <div class="composer-actions">
          <MicButton disabled={busy} onText={(text) => setDraft((prior) => appendSpoken(prior, text))} />
          <span class="composer-hint">
            Enter sends · Shift+Enter for a new line · / for commands
            {attachments.length ? ` · ${attachments.length} image${attachments.length > 1 ? 's' : ''} attached` : ''}
          </span>
          {busy && opId ? (
            <button type="button" onClick={() => void cancelTurn(opId)}>
              Stop
            </button>
          ) : (
            <button
              type="button"
              class="primary"
              disabled={!draft.trim() && !attachments.length}
              onClick={() => void submit(draft)}
            >
              Send
            </button>
          )}
        </div>
      </div>

      {questions && (
        <QuestionsPanel
          projectId={projectId}
          busy={busy}
          onAsk={(number) => void send(`edit ${number}`)}
          onClose={() => setQuestions(false)}
        />
      )}
    </div>
  );
}

function Row({ bubble, projectId }: { bubble: Bubble; projectId: string }) {
  if (bubble.role === 'card') {
    return (
      <div class="bubble card">
        <strong>{ARTIFACT_TITLES[bubble.kind ?? ''] ?? bubble.kind}</strong>
        <a class="card-note" href={`#/humans/planning/plan?id=${encodeURIComponent(projectId)}`}>
          Open the plan to read this in full →
        </a>
      </div>
    );
  }
  return (
    <div class={`bubble ${bubble.role}`}>
      <p>{bubble.text}</p>
    </div>
  );
}
