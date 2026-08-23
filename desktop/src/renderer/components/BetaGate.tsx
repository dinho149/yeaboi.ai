// The one-time notice in front of a beta mode.
//
// A BETA chip says *that* a mode is unverified and has nowhere to say how. This
// is the screen that says how — once, the first time the mode is opened, then
// never again. The copy is beta.py's, the same words the terminal shows, and
// the acknowledgement is the same one: dismissing it here means the terminal
// stops asking too.
//
// Only Continue writes the acknowledgement. Backing out leaves it pending, so
// someone who bailed still gets told next time.

import { Lozenge } from '@design/primitives';
import { useEffect, useState } from 'react';
import { type BetaGate as Gate, ackBetaGate, betaKeyFor, getBetaGates } from '../ambience';

export interface BetaGateProps {
  /** The route being entered. */
  path: string;
  /** Accepted — let the page through. */
  onContinue: () => void;
  /** Declined — go back where they came from. */
  onBack: () => void;
}

export function BetaGate({ path, onContinue, onBack }: BetaGateProps) {
  const key = betaKeyFor(path);
  const [gate, setGate] = useState<Gate | null>(null);
  const [chrome, setChrome] = useState({ label: 'BETA', subtitle: '', footer: '' });

  useEffect(() => {
    if (!key) {
      onContinue();
      return;
    }
    getBetaGates().then(
      (gates) => {
        const found = gates.gates[key];
        setChrome({ label: gates.label, subtitle: gates.subtitle, footer: gates.footer });
        if (!found || found.seen) onContinue();
        else setGate(found);
      },
      // A gate that cannot be read must not lock the mode out; the chip on the
      // page still carries the warning.
      () => onContinue(),
    );
  }, [key, onContinue]);

  if (!gate) return null;

  return (
    <div class="scrim">
      <div class="modal beta-gate" role="dialog" aria-modal="true" aria-label={gate.headline}>
        <header class="modal-head">
          <h2>{gate.headline}</h2>
          <Lozenge category="blocked">{chrome.label}</Lozenge>
        </header>
        {chrome.subtitle && <p class="modal-sub">{chrome.subtitle}</p>}
        {/* Blank lines in the copy are paragraph breaks, not filler. */}
        {gate.body
          .join('\n')
          .split('\n\n')
          .map((paragraph) => (
            <p key={paragraph.slice(0, 24)}>{paragraph.replace(/\n/g, ' ')}</p>
          ))}
        <p class="modal-foot">{chrome.footer}</p>
        <div class="modal-actions">
          <button
            type="button"
            class="primary"
            onClick={() => {
              void ackBetaGate(key).finally(onContinue);
            }}
          >
            Continue
          </button>
          <button type="button" onClick={onBack}>
            Back
          </button>
        </div>
      </div>
    </div>
  );
}
