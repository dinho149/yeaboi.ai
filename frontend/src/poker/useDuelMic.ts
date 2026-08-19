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
 * `getUserMedia` needs a secure context: localhost for the host, the HTTPS
 * tunnel for everyone else. Both qualify, so this should now always be capable —
 * but a browser can still refuse, and {@link micCapable} reports that so the UI
 * can say the host's room mic covers you, which is true.
 *
 * ## Lifetime
 *
 * The stream is a hardware handle and a recording indicator in the user's
 * browser chrome, and the two people holding one have different claims on it.
 *
 * A **duelist** armed theirs to argue, so it goes back when the floor closes —
 * along with the light, which is the failure people actually notice. The
 * **host's** is the room's mic and is held across tickets until they stop it or
 * leave: that is what "Recording session" means, and the board shows the room
 * that light on purpose. Both are released on unmount, and on any path that
 * calls `disable`.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { apiUrl, postJSON, type Session } from '../runtime/api';
import { read, remove, write } from '../runtime/storage';
import type { DuelSlice } from '../types/board';

/** Containers to try, best first. Safari has neither webm option. */
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

/** One retry, after a second. Fits inside the server's post-close grace window. */
const RETRY_MS = 1000;

/** Per-tab, so a reload can tell that it interrupted its own recording. */
const WAS_RECORDING = 'poker_room_mic';

export interface DuelMic {
  /** Whether this browser could record at all — secure context + an API. */
  capable: boolean;
  /** True once the user has granted permission and the stream is open. */
  armed: boolean;
  /** Set when permission was refused, for the UI to explain the fallback. */
  error: string;
  /** Ask for the mic. Must be called from a user gesture (the consent moment). */
  enable(): Promise<void>;
  /** Hand the hardware back and tell the room the indicator should go out. */
  disable(): void;
  /**
   * The board was recording when this page loaded, and this page is not the
   * one doing it — a reload, a crash, a closed tab. Host-only, and answered by
   * {@link enable} or {@link dismiss}.
   */
  interrupted: boolean;
  /** Leave it stopped. */
  dismiss(): void;
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

/**
 * Tell the room the light should be on, or out.
 *
 * The host's mic records the session, so it is board state and goes to the
 * admin route; a duelist's browser mic is one source inside a live duel, and
 * the board maps their pid to a role. Best-effort either way: a failure costs a
 * stale light until the round ends, which is not worth surfacing to somebody
 * who has just started talking.
 */
function micPath(session: Session): string {
  return session.admin ? '/api/admin/mic' : '/api/duel/mic';
}

function announce(session: Session, on: boolean): void {
  // `postJSON`, not a hand-rolled fetch: it is what merges `admin` into the
  // body, and the admin route is the whole reason this one has two paths.
  void postJSON(session, micPath(session), { on });
}

export function useDuelMic(session: Session, duel: DuelSlice | null, boardSaysRecording = false): DuelMic {
  const [armed, setArmed] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
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
    // Deliberate: this tab is not being interrupted next time it loads.
    remove('session', WAS_RECORDING);
    void announce(sessionRef.current, false);
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
    setInterrupted(false);
    write('session', WAS_RECORDING, '1');
    void announce(sessionRef.current, true);
  }, [capable]);

  const live = duel?.status === 'live';
  const role = duel?.mine_role ?? '';
  const myTurn = live && role !== '' && duel?.turn === role;
  const turnNo = duel?.turn_no ?? 0;
  /* The host records the room. Everyone is usually on one call, so one device
     catching both turns is the whole recording — and the host is the only
     person who is certainly there for all of it. */
  const roomMic = live && Boolean(session.admin);
  /* A floor that has been and gone, as opposed to one that never opened. */
  const floorOver = Boolean(duel) && !live;

  // Record my turn, or every turn if this is the room's mic. Keyed on the turn
  // number so the cleanup flushes each turn as its own clip: `onstop` is what
  // uploads, so a recorder left running across the hand-off would arrive as one
  // untellable blob.
  useEffect(() => {
    if (!armed || !(myTurn || roomMic)) {
      stopRecorder();
      return undefined;
    }
    startRecorder(turnNo);
    return () => stopRecorder();
  }, [armed, myTurn, roomMic, turnNo, startRecorder, stopRecorder]);

  /*
   * A duelist's mic belongs to the floor; the host's belongs to the session.
   *
   * Stopping the recorder above is not handing the hardware back: the tracks
   * stay open and so does the indicator in the browser's own chrome. For a
   * guest who armed their mic to argue, the floor closing is the end of
   * everything they had to record, and leaving the light on afterwards is the
   * failure people actually notice.
   *
   * The host's is deliberately not released here — "Recording session" means
   * armed once and held across tickets, which is the whole point of it, and the
   * room is shown that light on purpose.
   *
   * `floorOver` rather than `!live`, so arming before the host opens the floor
   * is not immediately undone by the effect that is meant to clean up after it.
   */
  useEffect(() => {
    if (!armed || session.admin || !floorOver) return;
    release();
  }, [armed, session.admin, floorOver, release]);

  /*
   * The light must not outlive the recording.
   *
   * A tab that is closed or reloaded takes the microphone with it, and a
   * `fetch` started during unload is cancelled with the document — so the flag
   * would stay on and every other screen would keep showing a red light for a
   * recording that stopped. `sendBeacon` is the one request the browser
   * promises to deliver after the page is gone.
   */
  useEffect(() => {
    if (!armed) return undefined;
    const clear = (): void => {
      const body = JSON.stringify({ pid: session.pid, admin: session.admin, on: false });
      navigator.sendBeacon?.(apiUrl(session, micPath(session)), new Blob([body], { type: 'application/json' }));
    };
    window.addEventListener('pagehide', clear);
    return () => window.removeEventListener('pagehide', clear);
  }, [armed, session]);

  /*
   * Was this tab recording when it went away?
   *
   * Not the board's flag: the beacon above clears that on the way out, so by
   * the time the new page asks, it says no. `sessionStorage` is the one thing
   * that survives a reload and dies with the tab, which is exactly the question
   * — and it is read once, so answering the notice ends it.
   */
  useEffect(() => {
    if (!session.admin || armed) return;
    if (read('session', WAS_RECORDING) !== '1') return;
    remove('session', WAS_RECORDING);
    setInterrupted(true);
    // And put the light out, since this tab's recording is the one that
    // stopped. Deliberately not "the board says recording and I am not doing
    // it": a host with a second tab open, or one who opens the board again on
    // another device, is not evidence that the first tab has stopped — and on
    // that reading every extra tab would extinguish a live recording.
    if (boardSaysRecording) announce(session, false);
  }, [session, boardSaysRecording, armed]);

  // Unmount: never leave the hardware held. Held in a ref so this runs on
  // unmount only, rather than every time `release` is re-created.
  const releaseRef = useRef(release);
  releaseRef.current = release;
  useEffect(() => () => releaseRef.current(), []);

  return { capable, armed, error, enable, disable: release, interrupted, dismiss: () => setInterrupted(false) };
}
