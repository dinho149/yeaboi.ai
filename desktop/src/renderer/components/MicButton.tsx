// Speak instead of typing. Drop this beside any text field.
//
// The terminal starts a take on a double-tap of the space bar, because a
// terminal cannot see a modifier chord on macOS and cannot detect key-release at
// all. Neither constraint exists here, so this is a button — press to start,
// press again to stop, Escape to throw the take away. That is the one place the
// two surfaces deliberately differ, and it is the surface's own idiom winning.
//
// The recording never leaves this machine: it goes to the local backend, which
// transcribes it with a model on this disk.

import { useEffect, useRef, useState } from 'react';
import {
  MAX_SECONDS,
  MicSession,
  SILENCE_LEVEL,
  SILENCE_SECONDS,
  type VoiceStatus,
  clock,
  getVoice,
  listMics,
  meterCells,
  pickMic,
  toBase64,
  transcribeAudio,
  transcribingLine,
} from '../voice';
import { VoiceSetup } from './VoiceSetup';

export interface MicButtonProps {
  /** The transcript, when there is one. Never called with an empty string. */
  onText: (text: string) => void;
  /** The field is busy doing something else — no new take should start. */
  disabled?: boolean;
}

type Phase = 'idle' | 'opening' | 'recording' | 'transcribing';

const METER_MS = 100;

export function MicButton({ onText, disabled }: MicButtonProps) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [setup, setSetup] = useState<VoiceStatus | null>(null);
  const [note, setNote] = useState('');
  const [level, setLevel] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [silent, setSilent] = useState(false);
  const session = useRef<MicSession | null>(null);
  const cancelled = useRef(false);

  // Released on unmount, or a field that closes mid-take leaves the microphone
  // light on until the window is closed.
  useEffect(() => () => session.current?.close(), []);

  useEffect(() => {
    if (phase !== 'recording') return;
    const started = Date.now();
    let heardAt = started;
    const timer = window.setInterval(() => {
      const live = session.current;
      if (!live) return;
      const peak = live.level();
      setLevel(peak);
      if (peak > SILENCE_LEVEL) heardAt = Date.now();
      setSilent(Date.now() - heardAt >= SILENCE_SECONDS * 1000);
      const seconds = (Date.now() - started) / 1000;
      setElapsed(seconds);
      if (seconds >= MAX_SECONDS) void finish();
    }, METER_MS);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    if (phase !== 'recording') return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      cancelled.current = true;
      void finish();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [phase]);

  async function begin(): Promise<void> {
    setNote('');
    setPhase('opening');
    let current = status;
    try {
      current = await getVoice();
      setStatus(current);
    } catch (error) {
      setPhase('idle');
      setNote((error as Error).message);
      return;
    }
    if (current.state !== 'ready') {
      setPhase('idle');
      setSetup(current);
      return;
    }
    try {
      const mics = await listMics();
      session.current = await MicSession.open(pickMic(mics, current.device), { record: true });
    } catch (error) {
      setPhase('idle');
      // The browser's own reason, not a house one: "denied", "in use by another
      // application" and "no such device" want three different responses, and a
      // single "could not access microphone" told you which one none of the time.
      setNote(`Microphone unavailable — ${(error as Error).message}`);
      return;
    }
    cancelled.current = false;
    setElapsed(0);
    setSilent(false);
    setPhase('recording');
  }

  async function finish(): Promise<void> {
    const live = session.current;
    session.current = null;
    if (!live) return;
    const blob = await live.stop();
    if (cancelled.current) {
      setPhase('idle');
      return;
    }
    if (!blob.size) {
      setPhase('idle');
      setNote('Nothing was recorded.');
      return;
    }
    setPhase('transcribing');
    try {
      const { text } = await transcribeAudio(await toBase64(blob), blob.type);
      setPhase('idle');
      // No quip here on purpose. The words landing in the box are the feedback;
      // a duck congratulating every sentence would be the ambient chatter the
      // voice deliberately has no tier for.
      if (text) onText(text);
      else setNote('No speech in that take.');
    } catch (error) {
      setPhase('idle');
      setNote((error as Error).message);
    }
  }

  const busy = phase === 'opening' || phase === 'transcribing';
  const bar = '▇'.repeat(meterCells(level)) + '▁'.repeat(8 - meterCells(level));

  return (
    <>
      <span class="mic">
        <button
          type="button"
          class={phase === 'recording' ? 'mic-button recording' : 'mic-button'}
          aria-pressed={phase === 'recording'}
          aria-label={phase === 'recording' ? 'Stop recording' : 'Dictate'}
          title={phase === 'recording' ? 'Stop and transcribe · Esc discards' : 'Speak instead of typing'}
          disabled={disabled || busy}
          onClick={() => (phase === 'recording' ? void finish() : void begin())}
        >
          🎤
        </button>
        {phase === 'recording' && (
          <span class="mic-status recording">
            <span class="mic-clock">{clock(elapsed)}</span>
            <span class="mic-meter">{bar}</span>
            {silent ? `no sound from ${session.current?.label ?? 'the mic'}` : 'Esc discards'}
          </span>
        )}
        {phase === 'transcribing' && (
          <span class="mic-status">{transcribingLine(status?.model_cached ?? true)}</span>
        )}
        {phase === 'idle' && note && (
          <span class="mic-status error" role="status">
            {note}
          </span>
        )}
      </span>

      {setup && (
        <VoiceSetup
          status={setup}
          onClose={(ready, message) => {
            setSetup(null);
            setNote(message);
            setStatus(null);
            if (ready) void begin();
          }}
        />
      )}
    </>
  );
}
