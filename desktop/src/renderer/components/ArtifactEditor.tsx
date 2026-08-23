// The correction panel — what a reader may fix on a generated artifact, and
// what has already been fixed.
//
// It goes through the same allowlist, the same caps and the same append-only
// log a teammate in the browser does: the edit is applied by
// `artifact_edit_apply`, never by writing to the store. That is the whole point
// of the engine — a correction made here and one made on a shared document are
// the same operation with the same refusals.
//
// Names in the history are **self-declared** (whoever held the link typed
// them), which is why this says so rather than presenting them as an audit
// trail.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { quip } from '../ambience';
import { type ArtifactEdits, type ArtifactRef, applyArtifactEdits, loadArtifactEdits } from '../boards';

export function ArtifactEditor({ refer, onApplied }: { refer: ArtifactRef; onApplied?: () => void }) {
  const [data, setData] = useState<ArtifactEdits | null>(null);
  const [error, setError] = useState('');
  const [path, setPath] = useState('');
  const [value, setValue] = useState('');
  const [author, setAuthor] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void refresh();
  }, [refer.kind, refer.session_id, refer.run_id]);

  async function refresh() {
    try {
      const next = await loadArtifactEdits(refer);
      setData(next);
      if (!path && next.artifact.fields.length) setPath(next.artifact.fields[0]!.path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function apply() {
    if (!path || !value.trim()) return;
    setBusy(true);
    setMessage('');
    try {
      const envelope = await applyArtifactEdits(refer, [{ op: 'set', path, value }], author);
      const result = envelope.data as { applied?: number; refused?: { reason: string }[] };
      if (result.refused?.length) {
        setMessage(`Refused: ${result.refused[0]!.reason}`);
      } else {
        setMessage(`Applied ${result.applied ?? 0}.`);
        setValue('');
        quip('artifact_done');
        onApplied?.();
      }
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  if (error) return <NoticeBlock title="Could not load the corrections" items={[error]} />;
  if (!data) return <p>Loading…</p>;

  const field = data.artifact.fields.find((row) => row.path === path);
  return (
    <Card title={`Correct this ${data.artifact.label.toLowerCase()}`}>
      {!data.artifact.headless && (
        <NoticeBlock
          title="Read-only here"
          items={[data.artifact.note || 'This artifact is only correctable on a shared document.']}
        />
      )}
      {data.artifact.headless && (
        <>
          <label class="field">
            <span>Field</span>
            <select value={path} onChange={(e) => setPath((e.target as HTMLSelectElement).value)}>
              {data.artifact.fields.map((row) => (
                <option key={row.path} value={row.path}>
                  {row.label}
                </option>
              ))}
            </select>
          </label>
          <label class="field">
            <span>New value{field ? ` (max ${field.max_length})` : ''}</span>
            <textarea
              rows={3}
              maxLength={field?.max_length}
              value={value}
              onInput={(e) => setValue((e.target as HTMLTextAreaElement).value)}
            />
          </label>
          <label class="field">
            <span>Your name</span>
            <input type="text" value={author} onInput={(e) => setAuthor((e.target as HTMLInputElement).value)} />
          </label>
          <button type="button" class="primary" disabled={busy || !value.trim()} onClick={() => void apply()}>
            {busy ? 'Applying…' : 'Apply correction'}
          </button>
        </>
      )}
      {message && <p class="dash-note">{message}</p>}
      <h3 class="card-subhead">
        {data.count} {data.count === 1 ? 'correction' : 'corrections'} on record
      </h3>
      {data.count > 0 && <p class="dash-note">Names are {data.attribution} — not an audit trail.</p>}
      <ul class="edit-log">
        {data.edits.map((edit) => (
          <li key={edit.id}>
            <strong>{edit.author || 'anonymous'}</strong> {edit.op} <code>{edit.path}</code>
            <span class="dash-note"> {edit.at}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
