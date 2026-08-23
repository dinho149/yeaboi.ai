// One engineer's file: the three workflows, the notes, and what is on record.
//
// The workflows go through the MCP tools — each is a single LLM call with no
// progress or cancel seam, so there is nothing to stream and a spinner is the
// honest affordance.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { maskText } from '../boards';
import {
  type EngineerFile,
  addNote,
  completeOneOnOne,
  loadEngineer,
  runPrep,
  runReview,
} from '../modes';
import { ResultActions } from '../components/ResultActions';

/** The engineer named in `#/humans/performance/engineer?name=…`. */
export function engineerFromHash(hash: string): string {
  const query = hash.split('?')[1] ?? '';
  return new URLSearchParams(query).get('name') ?? '';
}

type Busy = '' | 'prep' | 'complete' | 'review' | 'note';

export function PerformanceEngineer() {
  const name = engineerFromHash(window.location.hash);
  const [file, setFile] = useState<EngineerFile | null>(null);
  const [busy, setBusy] = useState<Busy>('');
  const [transcript, setTranscript] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');

  const reload = () => loadEngineer(name).then(setFile, (e: Error) => setError(e.message));

  useEffect(() => {
    if (name) void reload();
    // reload closes over `name`, which is the only thing it depends on.
  }, [name]);

  if (!name) return <NoticeBlock title="No engineer" items={['Pick someone from the roster first.']} />;
  if (error && !file) return <NoticeBlock title={`Nothing on file for ${name}`} items={[error]} />;
  if (!file) return <p>Loading…</p>;

  async function act(kind: Busy, call: () => Promise<{ ok: boolean; error?: { message: string } }>) {
    if (busy) return;
    setBusy(kind);
    setMessage('');
    setError('');
    try {
      const envelope = await call();
      if (!envelope.ok) setError(envelope.error?.message ?? 'The run failed.');
      else {
        setMessage('Done.');
        await reload();
      }
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy('');
  }

  const latest = file.latest;

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">{maskText(name, mask)}</h1>
          <p class="dash-sub">{latest ? maskText(latest.title, mask) : 'Nothing on file yet.'}</p>
        </div>
        <div class="dash-actions">
          <a class="button" href="#/humans/performance">
            Back
          </a>
        </div>
      </header>

      {anonNote && <NoticeBlock title={anonNote} items={['Review before sharing.']} />}
      {message && <p class="dash-note">{message}</p>}
      {error && <NoticeBlock title="That did not work" items={[error]} />}

      <div class="dash-actions">
        <button
          type="button"
          class="primary"
          disabled={Boolean(busy)}
          onClick={() => void act('prep', () => runPrep(name, ''))}
        >
          {busy === 'prep' ? 'Preparing…' : '1:1 Prep'}
        </button>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() => void act('review', () => runReview(name, ''))}
        >
          {busy === 'review' ? 'Reviewing…' : '6-month review'}
        </button>
      </div>

      {latest && (
        <Card title="Latest artifact">
          <p class="dash-note">{maskText(latest.title, mask)}</p>
          <ResultActions
            refer={{ kind: 'performance', session_id: name, run_id: 0 }}
            mode="performance"
            anonNote={anonNote}
            onAnonymize={(replacements, text) => {
              setMask(replacements);
              setAnonNote(text);
            }}
          />
        </Card>
      )}

      {file.open_actions.length > 0 && (
        <Card title="Open 1:1 actions">
          <ul class="review-list">
            {file.open_actions.map((action, i) => (
              <li key={`${action}-${i}`}>{maskText(action, mask)}</li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Complete a 1:1">
        <p class="dash-note">Paste the notes or the transcript; yeaboi summarises it and closes the open actions.</p>
        <textarea
          rows={6}
          value={transcript}
          placeholder="What you talked about…"
          onInput={(e) => setTranscript((e.target as HTMLTextAreaElement).value)}
        />
        <div class="dash-actions">
          <button
            type="button"
            disabled={Boolean(busy) || !transcript.trim()}
            onClick={() =>
              void act('complete', () => completeOneOnOne(name, transcript, '')).then(() => setTranscript(''))
            }
          >
            {busy === 'complete' ? 'Summarising…' : 'Complete'}
          </button>
        </div>
      </Card>

      <Card title="Notes">
        <div class="field-row">
          <input
            type="text"
            value={note}
            placeholder="Something worth remembering"
            onInput={(e) => setNote((e.target as HTMLInputElement).value)}
          />
          <button
            type="button"
            disabled={Boolean(busy) || !note.trim()}
            onClick={() => void act('note', () => addNote(name, note)).then(() => setNote(''))}
          >
            Add
          </button>
        </div>
        <ul class="review-list">
          {file.notes.map((row, i) => (
            <li key={`${row.created_at}-${i}`}>
              <strong>{row.created_at?.slice(0, 10)}</strong> {maskText(row.note ?? '', mask)}
            </li>
          ))}
        </ul>
      </Card>

      {file.history.length > 0 && (
        <Card title="History">
          <ul class="review-list">
            {file.history.map((row, i) => (
              <li key={i}>
                <strong>{String(row.kind ?? '')}</strong> {String(row.date ?? row.created_at ?? '')}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
