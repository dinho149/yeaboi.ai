// Dictation: the window records, the backend transcribes.
//
// The terminal owns both halves of this — PortAudio opens the microphone,
// negotiates a format it will accept, and hands WAV frames to Whisper. Here the
// first half is the browser engine's: getUserMedia picks the device, the OS
// draws its own permission prompt, and an AnalyserNode gives a level meter for
// free. So this module records a blob, ships it, and shows what comes back.
//
// The plumbing that touches the microphone lives in MicSession; everything a
// decision depends on is a pure function above it, which is what vitest covers.

import { apiGet, apiPost, apiStream } from './api';

export interface VoiceInstallOffer {
  available: boolean;
  blocked: string;
  size_mb: number;
  command: string;
}

export interface VoiceStatus {
  /** ready | installable | declined | unsupported */
  state: string;
  detail: string;
  model: string;
  model_cached: boolean;
  /** The VOICE_DEVICE preference, unresolved — a name, shared with the terminal. */
  device: string;
  install: VoiceInstallOffer;
  max_bytes: number;
}

export interface VoiceStageLine {
  type: string;
  stage?: string;
  detail?: string;
  fraction?: number | null;
  op_id?: string;
  message?: string;
  warning?: string;
}

export const getVoice = (): Promise<VoiceStatus> => apiGet('/api/voice');

export const setVoiceOffer = (enabled: boolean): Promise<{ enabled: boolean }> =>
  apiPost('/api/voice/offer', { enabled });

export const installVoice = (onLine: (line: VoiceStageLine) => void): Promise<void> =>
  apiStream('/api/voice/install', {}, (line) => onLine(line as VoiceStageLine));

export const cancelInstall = (opId: string): Promise<unknown> =>
  apiPost(`/api/ops/${encodeURIComponent(opId)}/cancel`);

export const transcribeAudio = (audio: string, mime: string): Promise<{ text: string }> =>
  apiPost('/api/voice/transcribe', { audio, mime });

// ── decisions ────────────────────────────────────────────────────────────────

/** Longest take. Past this the recorder stops itself and transcribes what it has. */
export const MAX_SECONDS = 120;

/** Opus at this rate keeps two minutes comfortably inside the backend's ceiling. */
export const AUDIO_BITRATE = 24_000;

/** A peak below this counts as silence — the terminal's threshold, unchanged. */
export const SILENCE_LEVEL = 0.02;

/** How long the meter stays flat before we say so. */
export const SILENCE_SECONDS = 2.5;

const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

/** The first container this engine will record that the backend can decode. */
export function recorderMime(isSupported: (mime: string) => boolean = defaultSupported): string {
  return MIME_CANDIDATES.find(isSupported) ?? '';
}

function defaultSupported(mime: string): boolean {
  return typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(mime);
}

export interface MicDevice {
  deviceId: string;
  label: string;
}

/**
 * The device id for a saved VOICE_DEVICE preference, or '' for the system default.
 *
 * The preference is a *name*, matched case-insensitively the way the terminal
 * matches it against PortAudio's list. A purely numeric preference is a
 * PortAudio index — it addresses a list this engine does not have, and matching
 * it as a substring would land on whichever microphone happens to have a digit
 * in its name, so it resolves to the default instead.
 */
export function pickMic(devices: MicDevice[], pref: string): string {
  const needle = pref.trim().toLowerCase();
  if (!needle || /^-?\d+$/.test(needle)) return '';
  return devices.find((device) => device.label.toLowerCase().includes(needle))?.deviceId ?? '';
}

/** Peak amplitude (0..1) from one AnalyserNode time-domain frame. */
export function meterLevel(frame: Uint8Array): number {
  let peak = 0;
  for (const sample of frame) {
    const amplitude = Math.abs(sample - 128) / 128;
    if (amplitude > peak) peak = amplitude;
  }
  return peak;
}

/**
 * Filled cells of an eight-cell level bar, square-rooted.
 *
 * The same curve the terminal's `level_meter` draws, for the same reason:
 * speech peaks land around 0.1–0.4 on a healthy microphone, and a linear bar
 * would barely move — which reads as a dead device rather than a quiet one.
 */
export function meterCells(level: number, cells = 8): number {
  return Math.round(Math.min(1, Math.max(0, level)) ** 0.5 * cells);
}

/**
 * Where a transcript goes in a field that already has something in it.
 *
 * Appended, never substituted: a second take is another sentence, and someone
 * who has typed half a thought and then spoken the rest must not lose the half
 * they typed. The terminal inserts at the cursor; a field with no cursor of its
 * own gets the end of what is there.
 */
export function appendSpoken(existing: string, spoken: string): string {
  return existing.trim() ? `${existing.replace(/\s+$/, '')} ${spoken}` : spoken;
}

export function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
}

/**
 * The status line for one frame of the setup flow.
 *
 * The same four stages the terminal animates, worded the same way — both
 * surfaces are driving one installer, and someone who has seen it in the
 * terminal should recognise it here.
 */
export function stageLine(line: VoiceStageLine): string {
  if (line.stage === 'install') return line.detail ? `Installing dictation — ${line.detail}` : 'Installing dictation…';
  if (line.stage === 'download') {
    if (line.fraction === null || line.fraction === undefined) {
      return `Speech model — ${line.detail || 'connecting'}…`;
    }
    const pct = `${Math.round(line.fraction * 100)}%`;
    return line.detail ? `Speech model ${pct} · ${line.detail}` : `Speech model ${pct}`;
  }
  if (line.stage === 'load') return 'Loading the speech model…';
  return 'Setting dictation up…';
}

/** What to say while a transcript is being made. */
export function transcribingLine(modelCached: boolean): string {
  return modelCached ? 'Transcribing your speech…' : 'Preparing the speech model (first run downloads it)…';
}

// ── the microphone ───────────────────────────────────────────────────────────

/**
 * One open microphone, with a live level and (optionally) a take being kept.
 *
 * Mirrors `voice.Recorder`, monitor mode included: the Settings mic test wants
 * the meter and nothing else, and holding on to those chunks would grow a
 * recording nobody ever asks for.
 */
export class MicSession {
  private chunks: Blob[] = [];
  private frame: Uint8Array<ArrayBuffer>;

  private constructor(
    private stream: MediaStream,
    private context: AudioContext,
    private analyser: AnalyserNode,
    readonly recorder: MediaRecorder | null,
    readonly mime: string,
    readonly label: string,
  ) {
    this.frame = new Uint8Array(analyser.fftSize);
  }

  /** Open `deviceId` ('' = the system default). Rejects with the browser's own reason. */
  static async open(deviceId: string, { record }: { record: boolean }): Promise<MicSession> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const label = stream.getAudioTracks()[0]?.label ?? 'the system default';

    let recorder: MediaRecorder | null = null;
    const mime = record ? recorderMime() : '';
    if (record) {
      recorder = new MediaRecorder(stream, {
        ...(mime ? { mimeType: mime } : {}),
        audioBitsPerSecond: AUDIO_BITRATE,
      });
    }
    const session = new MicSession(stream, context, analyser, recorder, mime || 'audio/webm', label);
    if (recorder) {
      recorder.ondataavailable = (event) => {
        if (event.data.size) session.chunks.push(event.data);
      };
      recorder.start();
    }
    return session;
  }

  level(): number {
    this.analyser.getByteTimeDomainData(this.frame);
    return meterLevel(this.frame);
  }

  /** Stop and return the take. Resolves with an empty blob in monitor mode. */
  async stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (!recorder || recorder.state === 'inactive') {
      this.close();
      return new Blob([], { type: this.mime });
    }
    const finished = new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
    });
    recorder.stop();
    await finished;
    this.close();
    return new Blob(this.chunks, { type: this.mime });
  }

  /** Release the device without keeping anything. Safe to call twice. */
  close(): void {
    for (const track of this.stream.getTracks()) track.stop();
    void this.context.close().catch(() => undefined);
  }
}

/**
 * A blob as base64, without its data: prefix.
 *
 * FileReader rather than btoa over the bytes: `String.fromCharCode(...bytes)`
 * spreads a whole recording onto the call stack and throws on anything longer
 * than a sentence.
 */
export function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('could not read the recording'));
    reader.onload = () => {
      const result = String(reader.result ?? '');
      resolve(result.slice(result.indexOf(',') + 1));
    };
    reader.readAsDataURL(blob);
  });
}

/** The input devices this engine can see, labelled. Labels need mic permission. */
export async function listMics(): Promise<MicDevice[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device) => ({ deviceId: device.deviceId, label: device.label }));
}
