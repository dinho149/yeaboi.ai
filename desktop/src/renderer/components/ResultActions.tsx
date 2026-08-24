// Export, Share and Anonymize — the three actions every result screen carries.
//
// One component rather than three copies per page: they take the same artifact
// reference, they open the same way, and a mode that grew its own copy of any
// of them would be the mode whose Share dialog forgets to say the link expired.
//
// Anonymize is deliberately NOT a state this component owns: the page has to
// mask what it is drawing, so the replacement map is handed up and the page
// applies it with `maskText`. A mask is a view over the same data, never a
// second copy of it.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { quip } from '../ambience';
import {
  type AnonLine,
  type AnonState,
  type ArtifactRef,
  type Destination,
  type KindCapability,
  type ShareSnapshot,
  anonymizeArtifact,
  closeShare,
  discardShareEdits,
  emptyAnon,
  exportArtifact,
  loadDestinations,
  loadKindCapabilities,
  loadShare,
  loadShares,
  reduceAnon,
  shareInvite,
  startShare,
} from '../boards';

type Dialog = '' | 'export' | 'share' | 'anonymize';

export interface ResultActionsProps {
  /** What these actions act on. */
  refer: ArtifactRef;
  /** The mode whose export folder and palette this belongs to. */
  mode: string;
  /** Mode-specific extra destinations (e.g. "jira" on a poker session). */
  extras?: string[];
  /** Non-empty while the page is showing masked data. */
  anonNote?: string;
  /** Handed the replacement map, or null to revert to the real names. */
  onAnonymize?: (replacements: [string, string][], note: string) => void;
}

export function ResultActions({ refer, mode, extras = [], anonNote = '', onAnonymize }: ResultActionsProps) {
  const [dialog, setDialog] = useState<Dialog>('');
  const [message, setMessage] = useState('');
  const [can, setCan] = useState<KindCapability | null>(null);

  useEffect(() => {
    // Which actions this kind supports is the backend's answer, not a table
    // kept here: a poker session exports and nothing else, and a button that
    // always refuses is worse than no button.
    loadKindCapabilities().then(
      (body) => setCan(body.kinds.find((row) => row.kind === refer.kind) ?? null),
      () => undefined,
    );
  }, [refer.kind]);

  return (
    <>
      <div class="dash-actions">
        {can?.export && (
          <button type="button" onClick={() => setDialog(dialog === 'export' ? '' : 'export')}>
            Export
          </button>
        )}
        {can?.share && (
          <button type="button" onClick={() => setDialog(dialog === 'share' ? '' : 'share')}>
            Share online
          </button>
        )}
        {can?.anonymize &&
          onAnonymize &&
          (anonNote ? (
            <>
              <button type="button" onClick={() => setDialog(dialog === 'anonymize' ? '' : 'anonymize')}>
                Adjust
              </button>
              <button type="button" onClick={() => onAnonymize([], '')}>
                Revert
              </button>
            </>
          ) : (
            <button type="button" onClick={() => setDialog(dialog === 'anonymize' ? '' : 'anonymize')}>
              Anonymize
            </button>
          ))}
      </div>
      {anonNote && <p class="anon-note">{anonNote}</p>}
      {message && <p class="dash-note">{message}</p>}
      {dialog === 'export' && (
        <ExportDialog
          refer={refer}
          mode={mode}
          extras={extras}
          onDone={(text) => {
            setMessage(text);
            setDialog('');
          }}
        />
      )}
      {dialog === 'share' && <ShareDialog refer={refer} onClose={() => setDialog('')} />}
      {dialog === 'anonymize' && (
        <AnonymizeDialog
          refer={refer}
          onDone={(replacements, note) => {
            onAnonymize?.(replacements, note);
            setDialog('');
          }}
          onCancel={() => setDialog('')}
        />
      )}
    </>
  );
}

// ── Export ─────────────────────────────────────────────────────────────────

function ExportDialog({
  refer,
  mode,
  extras,
  onDone,
}: {
  refer: ArtifactRef;
  mode: string;
  extras: string[];
  onDone: (message: string) => void;
}) {
  const [destinations, setDestinations] = useState<Destination[] | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    loadDestinations(mode, extras).then(
      (body) => setDestinations(body.destinations),
      (e: Error) => setError(e.message),
    );
  }, [mode]);

  async function send(destination: Destination) {
    setBusy(destination.key);
    setError('');
    try {
      const result = await exportArtifact(refer, destination.key);
      if (destination.local && result.markdown !== undefined) {
        // The clipboard belongs to whoever is in front of the screen, not to a
        // background process — the backend hands over the text and stops there.
        await navigator.clipboard.writeText(result.markdown);
        quip('export_done');
        onDone('Copied the Markdown to your clipboard.');
        return;
      }
      quip('export_done');
      onDone(result.message ?? 'Exported.');
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy('');
  }

  return (
    <Card title="Choose export destination">
      {error && <NoticeBlock title="Export failed" items={[error]} />}
      {!destinations && <p>Loading…</p>}
      <ul class="dest-list">
        {(destinations ?? []).map((destination) => (
          <li key={destination.key}>
            <button
              type="button"
              disabled={Boolean(busy) || Boolean(destination.blocked)}
              onClick={() => void send(destination)}
            >
              {busy === destination.key ? 'Sending…' : destination.label}
            </button>
            <span class="dest-note">{destination.blocked || destination.description}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ── Share ──────────────────────────────────────────────────────────────────

function ShareDialog({ refer, onClose }: { refer: ArtifactRef; onClose: () => void }) {
  const [share, setShare] = useState<ShareSnapshot | null>(null);
  const [invite, setInvite] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Rejoin the share this artifact already has rather than opening a second
    // one: the session lives in the backend precisely so a reload does not
    // strand a tunnel.
    loadShares().then(
      (body) => {
        const open = body.shares.find(
          (row) => row.kind === refer.kind && row.session_id === (refer.session_id ?? ''),
        );
        if (open) setShare(open);
      },
      () => undefined,
    );
  }, [refer.kind, refer.session_id]);

  // Poll while the link is still coming up: setup is a binary download plus an
  // edge handshake, and the page has nothing to show until it lands.
  useEffect(() => {
    if (!share || share.link.state === 'ready' || share.link.state === 'failed' || share.link.state === 'off') return;
    const timer = setInterval(() => {
      loadShare(share.share_id).then(setShare, () => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [share?.share_id, share?.link.state]);

  useEffect(() => {
    if (!share || share.link.state !== 'ready') return;
    shareInvite(share.share_id).then((body) => setInvite(body.invite), () => undefined);
  }, [share?.share_id, share?.link.state]);

  async function begin() {
    setBusy(true);
    setError('');
    try {
      setShare(await startShare(refer));
      quip('link_ready');
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  async function stop(commit: boolean) {
    if (!share) return;
    setBusy(true);
    const result = await closeShare(share.share_id, commit);
    setBusy(false);
    setMessage(result.message);
    setShare(null);
    onClose();
  }

  async function discard() {
    if (!share) return;
    const result = await discardShareEdits(share.share_id);
    setMessage(result.message);
    setShare(result.share);
  }

  if (!share) {
    return (
      <Card title="Share this output online">
        <p class="dash-note">
          Anyone with the temporary URL and the access code can read this while the share is open.
        </p>
        {error && <NoticeBlock title="Could not start the share" items={[error]} />}
        {message && <p class="dash-note">{message}</p>}
        <button type="button" class="primary" disabled={busy} onClick={() => void begin()}>
          {busy ? 'Starting…' : 'Start sharing'}
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </Card>
    );
  }

  return (
    <Card title={`Sharing — ${share.title}`}>
      {/* The link's own notice wins over everything else on this card: it is
          only non-empty for an expiry, and once a quick tunnel expires the
          invite already sent to everyone is permanently dead. */}
      {share.link.notice && <NoticeBlock title="Secure link" items={[share.link.notice]} />}
      <p class="dash-note">{share.link.status}</p>
      {share.link.state === 'ready' ? (
        <>
          <dl class="share-facts">
            <dt>Public URL</dt>
            <dd>{share.share_url}</dd>
            <dt>Access code</dt>
            <dd>{share.display_code}</dd>
          </dl>
          <button
            type="button"
            disabled={!invite}
            onClick={() => void navigator.clipboard.writeText(invite)}
          >
            Copy invite
          </button>
        </>
      ) : (
        <p class="dash-note">The access code is already live; the link takes a few seconds.</p>
      )}
      {share.editable && (
        <p class="dash-note">
          {share.edits
            ? `${share.edits} ${share.edits === 1 ? 'correction' : 'corrections'} by ${share.editors.length || 'someone'}.`
            : 'Teammates can correct what the run got wrong.'}
        </p>
      )}
      {share.edits > 0 && (
        <button type="button" onClick={() => void discard()}>
          Discard edits
        </button>
      )}
      <div class="dash-actions">
        {/* Keeping somebody else's corrections is the host's decision, so the
            two ways to stop are two buttons rather than one and a default. */}
        {share.edits > 0 && (
          <button type="button" class="primary" disabled={busy} onClick={() => void stop(true)}>
            Keep {share.edits === 1 ? 'the correction' : 'the corrections'} &amp; stop
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => void stop(false)}>
          Stop sharing
        </button>
      </div>
    </Card>
  );
}

// ── Anonymize ──────────────────────────────────────────────────────────────

function AnonymizeDialog({
  refer,
  onDone,
  onCancel,
}: {
  refer: ArtifactRef;
  onDone: (replacements: [string, string][], note: string) => void;
  onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [state, setState] = useState<AnonState>(emptyAnon());
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    let next = emptyAnon();
    setState(next);
    try {
      await anonymizeArtifact(refer, instruction, (line: AnonLine) => {
        next = reduceAnon(next, line);
        setState(next);
      });
    } catch (e) {
      next = { ...next, error: (e as Error).message, finished: true };
      setState(next);
    }
    setBusy(false);
    if (!next.error) {
      quip('anonymize_done');
      onDone(next.replacements, next.note);
    }
  }

  return (
    <Card title="Anonymize this output">
      <p class="dash-note">
        Names, tickets and identifiers are replaced with stable placeholders. Review before sharing — a mask is a
        starting position, not a guarantee.
      </p>
      <label class="field">
        <span>Also mask … · don&apos;t mask … (it&apos;s public/safe)</span>
        <input
          type="text"
          value={instruction}
          onInput={(e) => setInstruction((e.target as HTMLInputElement).value)}
        />
      </label>
      {state.phases.length > 0 && <p class="dash-note">{state.phases[state.phases.length - 1]}</p>}
      {state.error && <NoticeBlock title="Anonymize failed" items={[state.error]} />}
      {state.warnings.length > 0 && <NoticeBlock title="Notices" items={state.warnings} />}
      <div class="dash-actions">
        <button type="button" class="primary" disabled={busy} onClick={() => void run()}>
          {busy ? 'Masking…' : 'Mask'}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Card>
  );
}
