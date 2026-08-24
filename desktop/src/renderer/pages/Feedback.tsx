// Feedback — a bug, a request, or a complaint, filed as a GitHub issue.
//
// Two buttons, because there are two things a person might want. AI Polish
// rewrites the draft into something a maintainer can act on and hands it back
// for review; it never submits, and when no model is configured the answer is
// the draft that was already written. Submit files it: through the API when a
// GitHub token is configured, and otherwise by opening a pre-filled issue form
// in the browser, since the repository is public.

import { Card, NoticeBlock } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import {
  type FeedbackOptions,
  type FeedbackResult,
  duckVoice,
  getFeedbackOptions,
  polishFeedback,
  submitFeedback,
} from '../ambience';
import { MicButton } from '../components/MicButton';
import { appendSpoken } from '../voice';

export function Feedback() {
  const [options, setOptions] = useState<FeedbackOptions | null>(null);
  const [kind, setKind] = useState('Bug');
  const [area, setArea] = useState('general');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState<FeedbackResult | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    getFeedbackOptions().then(setOptions, (e: Error) => setError(e.message));
  }, []);

  if (error) return <NoticeBlock title="Could not open the feedback form" items={[error]} />;
  if (!options) return <p>Loading…</p>;

  const draft = { kind, area, title, description };
  const ready = title.trim().length > 0 && description.trim().length > 0;

  function polish(): void {
    setBusy('polish');
    setStatus('');
    polishFeedback(draft)
      .then((answer) => {
        if (answer.polished) {
          setTitle(answer.polished.title);
          setDescription(answer.polished.description);
        }
        setStatus(answer.status);
      }, (e: Error) => setStatus(e.message))
      .finally(() => setBusy(''));
  }

  function send(): void {
    setBusy('submit');
    setStatus('');
    submitFeedback(draft)
      .then((answer) => {
        setResult(answer);
        if (answer.ok) duckVoice().say('Sent it!');
      }, (e: Error) => setStatus(e.message))
      .finally(() => setBusy(''));
  }

  if (result) {
    return (
      <div class="dash">
        <h1 class="page-title">Feedback</h1>
        <Card title={result.ok ? 'Thank you' : 'Not filed yet'}>
          <p>
            <Duck state={result.ok ? 'idle' : 'offline'} size={28} /> {result.message}
          </p>
          {result.url && (
            <p>
              <a href={result.url} target="_blank" rel="noreferrer">
                {result.url}
              </a>
            </p>
          )}
          <div class="modal-actions">
            <button
              type="button"
              onClick={() => {
                setResult(null);
                setTitle('');
                setDescription('');
              }}
            >
              Write another
            </button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Feedback</h1>
          <p class="dash-sub">
            Files an issue on <code>{options.repo}</code>. Nothing is sent until you press Submit.
          </p>
        </div>
      </header>

      <Card title="What happened">
        <div class="settings-row">
          <span class="settings-label">Type</span>
          <div class="settings-choices">
            {options.types.map((option) => (
              <button
                key={option}
                type="button"
                class={option === kind ? 'choice active' : 'choice'}
                onClick={() => setKind(option)}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <div class="settings-row">
          <span class="settings-label">Area</span>
          <select value={area} onChange={(event) => setArea((event.target as HTMLSelectElement).value)}>
            {options.areas.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <label class="field">
          <span class="settings-label">Title</span>
          <input
            type="text"
            value={title}
            placeholder="One line — what went wrong, or what is missing"
            onInput={(event) => setTitle((event.target as HTMLInputElement).value)}
          />
        </label>
        <label class="field">
          <span class="settings-label">Description</span>
          <textarea
            rows={10}
            value={description}
            placeholder="What you did, what you expected, what happened instead."
            onInput={(event) => setDescription((event.target as HTMLTextAreaElement).value)}
          />
          <MicButton onText={(text) => setDescription((prior) => appendSpoken(prior, text))} />
        </label>
        {status && <p class="settings-status">{status}</p>}
        <div class="modal-actions">
          <button type="button" class="primary" disabled={!ready || busy !== ''} onClick={send}>
            {busy === 'submit' ? 'Submitting…' : 'Submit'}
          </button>
          <button type="button" disabled={!ready || busy !== ''} onClick={polish}>
            {busy === 'polish' ? 'Polishing…' : 'AI Polish'}
          </button>
        </div>
      </Card>
    </div>
  );
}
