/**
 * Recording your own turn on the open floor.
 *
 * When the host opens the floor, the low and high voters get timed turns to
 * argue their estimates. Whatever is captured is transcribed and handed to the
 * AI for its verdict, so the recording has to be attributed: each duelist
 * records *their own* turn from their own device, and the server stitches the
 * segments together in turn order.
 *
 * ## Why the choreography is driven by the snapshot
 *
 * There is no "your turn now" event. The turn lives in the board state and
 * arrives with every poll, so this hook watches `duel.turn` and starts or stops
 * the recorder when it crosses. That is deliberate rather than a limitation: a
 * missed event would leave a duelist recording through someone else's turn, or
 * silently not recording at all, whereas a missed *poll* self-corrects on the
 * next one. The old page did the same thing from inside its render function.
 *
 * ## Secure context
 *
 * `getUserMedia` needs a secure context, so this works on localhost and over
 * the HTTPS tunnel but not on a plain-HTTP LAN address. That is not a bug to
 * route around — {@link micCapable} reports it so the UI can say the host's
 * room mic covers you, which is true.
 *
 * ## Lifetime
 *
 * The stream is a hardware handle and a recording indicator in the user's
 * browser chrome. It is released when the duel ends, when the component
 * unmounts, and on any path that stops the recorder — never left open between
 * rounds, which is the failure people actually notice.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { apiUrl, type Session } from '../runtime/api';
import type { DuelSlice } from '../types/board';

/** Containers to try, best first. Safari has neither webm option. */
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

/** One retry, after a second. Fits inside the server's post-close grace window. */
const RETRY_MS = 1000;

export interface DuelMic {
  /** Whether this browser could record at all — secure context + an API. */
  capable: boolean;
  /** True once the user has granted permission and the stream is open. */
  armed: boolean;
  /** Set when permission was refused, for the UI to explain the fallback. */
  error: string;
  /** Ask for the mic. Must be called from a user gesture (the consent moment). */
  enable(): Promise<void>;
}

export function micCapable(): boolean {
  if (typeof window === 'undefined' || !window.isSecureContext) return false;
  // `navigator.mediaDevices` is typed non-optional by lib.dom, but it is
  // genuinely absent outside a secure context and in some embedded webviews —
  // hence the cast rather than an optional chain TypeScript would call dead.
  const media = navigator.mediaDevices as MediaDevices | undefined;
  return typeof media?.getUserMedia === 'function' && typeof MediaRecorder !== 'undefined';
}

function pickMimeType(): MediaRecorderOptions {
  for (const mimeType of MIME_TYPES) {
    if (MediaRecorder.isTypeSupported?.(mimeType)) return { mimeType };
  }
  return {};
}

/**
 * Upload one turn's audio.
 *
 * Retried once on a 5xx or a network failure, because the recording cannot be
 * reproduced — the moment has passed and the person has stopped talking. A 4xx
 * is not retried: the server has decided this segment does not belong to this
 * duel, and sending it again will not change its mind.
 */
async function upload(session: Session, blob: Blob, turn: number, attempt = 1): Promise<void> {
  const url = apiUrl(session, '/api/duel/audio', { pid: session.pid, turn: String(turn) });
  try {
    const response = await fetch(url, { method: 'POST', body: blob });
    if (!response.ok && response.status >= 500 && attempt === 1) {
      setTimeout(() => void upload(session, blob, turn, 2), RETRY_MS);
    }
  } catch {
    if (attempt === 1) setTimeout(() => void upload(session, blob, turn, 2), RETRY_MS);
  }
}

export function useDuelMic(session: Session, duel: DuelSlice | null): DuelMic {
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState('');
  const [capable] = useState(micCapable);

  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  // Read inside callbacks that must not be re-created when the snapshot ticks.
  const sessionRef = useRef(session);
  sessionRef.current = session;

  const stopRecorder = useCallback(() => {
    if (!recorder.current) return;
    try {
      // `stop()` fires `onstop`, which is what actually uploads. Tearing the
      // recorder down without it would drop the turn that just ended.
      recorder.current.stop();
    } catch {
      /* already inactive — nothing to flush */
    }
    recorder.current = null;
  }, []);

  const release = useCallback(() => {
    stopRecorder();
    if (!stream.current) return;
    for (const track of stream.current.getTracks()) track.stop();
    stream.current = null;
    setArmed(false);
    // Best-effort: tell the room the indicator should go out. A failure here
    // costs a stale 🎙 on other screens until the duel ends, which is not worth
    // surfacing to someone who has just stopped talking.
    void fetch(apiUrl(sessionRef.current, '/api/duel/mic'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: sessionRef.current.pid, on: false }),
    }).catch(() => {});
  }, [stopRecorder]);

  const startRecorder = useCallback((turnNo: number) => {
    if (!stream.current || recorder.current) return;
    let next: MediaRecorder;
    try {
      next = new MediaRecorder(stream.current, pickMimeType());
    } catch {
      // A browser that rejects every container we named can still usually
      // record in its own default one.
      try {
        next = new MediaRecorder(stream.current);
      } catch {
        return;
      }
    }
    chunks.current = [];
    next.ondataavailable = (event) => {
      if (event.data?.size) chunks.current.push(event.data);
    };
    next.onstop = () => {
      const blob = new Blob(chunks.current);
      chunks.current = [];
      if (blob.size) void upload(sessionRef.current, blob, turnNo);
    };
    recorder.current = next;
    next.start();
  }, []);

  const enable = useCallback(async () => {
    if (!capable || stream.current) return;
    try {
      // The tap that got here is the user gesture; the OS prompt follows.
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError('Mic permission denied — the room mic covers you.');
      return;
    }
    setError('');
    setArmed(true);
    void fetch(apiUrl(sessionRef.current, '/api/duel/mic'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: sessionRef.current.pid, on: true }),
    }).catch(() => {});
  }, [capable]);

  const live = duel?.status === 'live';
  const role = duel?.mine_role ?? '';
  const myTurn = live && role !== '' && duel?.turn === role;
  const turnNo = duel?.turn_no ?? 0;

  // The choreography: record during my turn and at no other time.
  useEffect(() => {
    if (!armed) return;
    if (myTurn) startRecorder(turnNo);
    else stopRecorder();
  }, [armed, myTurn, turnNo, startRecorder, stopRecorder]);

  // The floor closed — mid-turn or not. `stopRecorder` before `release` is what
  // flushes a turn that was still running when the host closed the floor.
  useEffect(() => {
    if (!live && armed) release();
  }, [live, armed, release]);

  // Unmount: never leave the hardware held. Held in a ref so this runs on
  // unmount only, rather than every time `release` is re-created.
  const releaseRef = useRef(release);
  releaseRef.current = release;
  useEffect(() => () => releaseRef.current(), []);

  return { capable, armed, error, enable };
}
