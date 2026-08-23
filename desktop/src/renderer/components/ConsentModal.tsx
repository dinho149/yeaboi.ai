// The filesystem-sandbox consent modal.
//
// yeaboi may only touch its own data directory unless a path is allowed. When
// something is refused, the backend queues the denial and announces it on the
// ambient feed; this is where the person answers. The three answers and what
// each one means are the sandbox's, not this modal's — the same three the
// terminal's popup offers.
//
// The access that triggered the request has already failed. Consent is for the
// retry, which is why the modal says what was refused rather than pretending to
// hold anything up.

import { useEffect, useState } from 'react';
import { type ConsentRequest, getConsentRequests, resolveConsent } from '../ambience';

const CHOICES: ReadonlyArray<readonly [string, string, string]> = [
  ['allow_once', 'Allow once', 'For this run only — forgotten when yeaboi exits'],
  ['allow_always', 'Always allow', 'Added to the whitelist in ~/.yeaboi/.env'],
  ['deny', 'Deny', 'yeaboi will not touch this path'],
];

export interface ConsentModalProps {
  /** Bumped whenever a consent_request arrives on the ambient feed. */
  signal: number;
}

export function ConsentModal({ signal }: ConsentModalProps) {
  const [queue, setQueue] = useState<ConsentRequest[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Read the queue rather than trust the event: a window that reloaded
    // between the denial and the click would otherwise show nothing at all.
    getConsentRequests().then(
      (page) => setQueue(page.requests),
      () => setQueue([]),
    );
  }, [signal]);

  const request = queue[0];
  if (!request) return null;

  function answer(choice: string): void {
    if (!request) return;
    setBusy(true);
    resolveConsent(request.req_id, choice).finally(() => {
      setBusy(false);
      setQueue((rest) => rest.slice(1));
    });
  }

  const verb = request.mode === 'write' ? 'write to' : 'read from';
  const feature = request.context || 'A feature';

  return (
    <div class="scrim">
      <div class="modal consent" role="dialog" aria-modal="true" aria-label="Allow file access?">
        <header class="modal-head">
          <h2>Allow file access?</h2>
        </header>
        <p>
          <strong>{feature}</strong> wants to {verb}:
        </p>
        <p class="consent-path">{request.path}</p>
        <p class="modal-foot">yeaboi only accesses ~/.yeaboi unless you allow a path.</p>
        <div class="modal-actions">
          {CHOICES.map(([choice, label, hint]) => (
            <button
              key={choice}
              type="button"
              class={choice === 'allow_once' ? 'primary' : undefined}
              title={hint}
              disabled={busy}
              onClick={() => answer(choice)}
            >
              {label}
            </button>
          ))}
        </div>
        {queue.length > 1 && <p class="modal-foot">{queue.length - 1} more waiting.</p>}
      </div>
    </div>
  );
}
