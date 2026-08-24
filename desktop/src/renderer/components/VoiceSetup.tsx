// The "shall I set dictation up?" modal, and the install it runs.
//
// Speech recognition is an optional extra because its wheels do not exist for
// every machine — so the app ships without it and offers to fetch it the first
// time someone presses the microphone. The terminal makes the same offer from a
// one-line status bar; this has room for the whole sentence, but the answer is
// the same three: install it, not now, or never.
//
// "Never" is persisted and shared: declining here stops the terminal asking too,
// and Settings is the way back.

import { type ReactNode } from 'react';
import { useRef, useState } from 'react';
import {
  type VoiceStageLine,
  type VoiceStatus,
  cancelInstall,
  installVoice,
  setVoiceOffer,
  stageLine,
} from '../voice';

export interface VoiceSetupProps {
  status: VoiceStatus;
  /** Called with the outcome: `true` once dictation can actually be used. */
  onClose: (ready: boolean, message: string) => void;
}

export function VoiceSetup({ status, onClose }: VoiceSetupProps) {
  const [running, setRunning] = useState(false);
  const [line, setLine] = useState('');
  const [fraction, setFraction] = useState<number | null>(null);
  const opId = useRef('');

  if (status.state === 'unsupported' || status.install.blocked) {
    return (
      <Shell title="Dictation can't run here">
        <p>{status.install.blocked || status.detail}</p>
        <div class="modal-actions">
          <button type="button" class="primary" onClick={() => onClose(false, '')}>
            Close
          </button>
        </div>
      </Shell>
    );
  }

  function start(): void {
    setRunning(true);
    setLine('Setting dictation up…');
    let failure = '';
    let warning = '';
    installVoice((event: VoiceStageLine) => {
      if (event.type === 'op') opId.current = event.op_id ?? '';
      else if (event.type === 'stage') {
        setLine(stageLine(event));
        setFraction(event.fraction ?? null);
      } else if (event.type === 'error') failure = event.message ?? 'Dictation setup failed';
      else if (event.type === 'done') warning = event.warning ?? '';
    })
      .then(
        () => onClose(!failure, failure || warning),
        (error: Error) => onClose(false, error.message),
      )
      .finally(() => setRunning(false));
  }

  if (running) {
    return (
      <Shell title="Setting dictation up">
        <p class="voice-stage">{line}</p>
        <div class="voice-bar" role="progressbar" aria-valuenow={fraction === null ? undefined : fraction * 100}>
          <span class={fraction === null ? 'voice-bar-fill indeterminate' : 'voice-bar-fill'} style={barStyle(fraction)} />
        </div>
        <p class="modal-foot">This happens once. You can keep working — it runs in the background.</p>
        <div class="modal-actions">
          <button
            type="button"
            onClick={() => {
              if (opId.current) void cancelInstall(opId.current);
            }}
          >
            Cancel
          </button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell title="Set dictation up?">
      <p>
        Speaking instead of typing needs a speech engine — about {status.install.size_mb} MB, downloaded once. It runs
        entirely on this machine: nothing you say is sent anywhere.
      </p>
      <p class="modal-foot">Takes about two minutes.</p>
      <div class="modal-actions">
        <button type="button" class="primary" onClick={start}>
          Install
        </button>
        <button type="button" onClick={() => onClose(false, '')}>
          Not now
        </button>
        <button
          type="button"
          onClick={() => {
            void setVoiceOffer(false);
            onClose(false, 'Dictation is off. Turn it back on in Settings → Voice Input.');
          }}
        >
          Never
        </button>
      </div>
    </Shell>
  );
}

function barStyle(fraction: number | null): string {
  return fraction === null ? '' : `width: ${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
}

// Escape is deliberately not wired anywhere in this modal: the install spawns a
// package manager, and a stray keystroke must not walk away from a half-written
// environment. Cancel is a button, and it stops the child properly.
function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div class="scrim">
      <div class="modal voice-setup" role="dialog" aria-modal="true" aria-label={title}>
        <header class="modal-head">
          <h2>{title}</h2>
        </header>
        {children}
      </div>
    </div>
  );
}
