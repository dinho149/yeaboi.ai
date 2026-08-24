// The decisions dictation makes before it touches a microphone: which device a
// saved preference means, what the meter shows, where a transcript goes, and
// what the installer is saying. MicSession itself is browser plumbing — the
// route tests cover the far end of it.

import { describe, expect, it } from 'vitest';
import {
  appendSpoken,
  clock,
  meterCells,
  meterLevel,
  pickMic,
  recorderMime,
  stageLine,
  transcribingLine,
} from '../src/renderer/voice';

const MICS = [
  { deviceId: 'default-id', label: 'MacBook Pro Microphone' },
  { deviceId: 'shure-id', label: 'Shure MV7' },
  { deviceId: 'cam-id', label: 'Studio 26 Webcam' },
];

describe('pickMic', () => {
  it('matches the saved name the way the terminal matches it', () => {
    expect(pickMic(MICS, 'shure')).toBe('shure-id');
    expect(pickMic(MICS, 'MacBook')).toBe('default-id');
  });

  it('falls back to the system default when nothing matches', () => {
    // An unset or unmatched preference must never block a recording.
    expect(pickMic(MICS, '')).toBe('');
    expect(pickMic(MICS, 'Blue Yeti')).toBe('');
  });

  it('treats a PortAudio index as "the default", not as a substring', () => {
    // "2" is an index into a list this engine does not have; matched as text it
    // would land on the webcam, which is not the microphone anybody chose.
    expect(pickMic(MICS, '2')).toBe('');
    expect(pickMic(MICS, '-1')).toBe('');
  });
});

describe('the level meter', () => {
  it('reads a peak off a time-domain frame', () => {
    expect(meterLevel(Uint8Array.from([128, 128, 128]))).toBe(0);
    expect(meterLevel(Uint8Array.from([128, 192, 128]))).toBeCloseTo(0.5);
    expect(meterLevel(Uint8Array.from([128, 64, 128]))).toBeCloseTo(0.5);
  });

  it('curves the bar so speech actually moves it', () => {
    // A linear bar would show one cell for a healthy 0.16 peak, which reads as
    // a dead microphone. Square-rooted, the same peak fills three.
    expect(meterCells(0)).toBe(0);
    expect(meterCells(0.16)).toBe(3);
    expect(meterCells(1)).toBe(8);
  });

  it('clamps rather than overflowing the bar', () => {
    expect(meterCells(-1)).toBe(0);
    expect(meterCells(4)).toBe(8);
  });
});

describe('appendSpoken', () => {
  it('adds to what is already there rather than replacing it', () => {
    expect(appendSpoken('We need', 'a booking flow.')).toBe('We need a booking flow.');
  });

  it('does not leave a leading space in an empty field', () => {
    expect(appendSpoken('', 'Hello.')).toBe('Hello.');
    expect(appendSpoken('   ', 'Hello.')).toBe('Hello.');
  });

  it('does not double the space after a trailing one', () => {
    expect(appendSpoken('We need ', 'more.')).toBe('We need more.');
  });
});

describe('recorderMime', () => {
  it('prefers opus in webm', () => {
    expect(recorderMime(() => true)).toBe('audio/webm;codecs=opus');
  });

  it('falls back to what the engine will actually record', () => {
    expect(recorderMime((mime) => mime === 'audio/mp4')).toBe('audio/mp4');
  });

  it('is empty when the engine records nothing we can decode', () => {
    expect(recorderMime(() => false)).toBe('');
  });
});

describe('stageLine', () => {
  it('names the packages stage with whatever the installer just said', () => {
    expect(stageLine({ type: 'stage', stage: 'install', detail: 'resolving faster-whisper' })).toBe(
      'Installing dictation — resolving faster-whisper',
    );
    expect(stageLine({ type: 'stage', stage: 'install', detail: '' })).toBe('Installing dictation…');
  });

  it('reports a real percentage once there is one', () => {
    expect(stageLine({ type: 'stage', stage: 'download', fraction: 0.5, detail: '70/145 MB' })).toBe(
      'Speech model 50% · 70/145 MB',
    );
  });

  it('does not invent a percentage before the total is known', () => {
    expect(stageLine({ type: 'stage', stage: 'download', fraction: null, detail: '' })).toBe(
      'Speech model — connecting…',
    );
  });

  it('says the model is loading, which is not the same as downloading', () => {
    expect(stageLine({ type: 'stage', stage: 'load' })).toBe('Loading the speech model…');
  });
});

describe('transcribingLine', () => {
  it('warns that a first run has a download in it', () => {
    // Otherwise a cold model reads as a hang.
    expect(transcribingLine(false)).toContain('first run');
    expect(transcribingLine(true)).toBe('Transcribing your speech…');
  });
});

describe('clock', () => {
  it('counts the take in minutes and seconds', () => {
    expect(clock(0)).toBe('0:00');
    expect(clock(9.7)).toBe('0:09');
    expect(clock(75)).toBe('1:15');
  });
});
