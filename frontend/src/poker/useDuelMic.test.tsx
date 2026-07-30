/**
 * The duel recorder.
 *
 * The three failures worth guarding, in order of how bad they are:
 *
 * 1. **Recording through someone else's turn.** The transcript is attributed by
 *    turn, so a recorder left running past your slot puts your voice in the
 *    other duelist's segment and the AI reads it as their argument.
 * 2. **Never releasing the mic.** The stream is a hardware handle and a
 *    recording dot in the browser chrome. Leaving it open after the floor
 *    closes is the kind of thing people notice and do not forgive.
 * 3. **Dropping the last turn.** Closing the floor mid-turn has to flush what
 *    was captured, because the moment cannot be replayed.
 */

import { render } from '@testing-library/preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Session } from '../runtime/api';
import type { DuelSlice } from '../types/board';
import { useDuelMic } from './useDuelMic';

const SESSION: Session = { token: 't', admin: '', pid: 'pid-a' };

/** A MediaRecorder stand-in that records what was asked of it. */
class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported = (): boolean => true;

  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  started = false;
  stopped = false;

  constructor() {
    FakeRecorder.instances.push(this);
  }

  start(): void {
    this.started = true;
  }

  stop(): void {
    this.stopped = true;
    // The real thing delivers a final chunk and then fires onstop; the upload
    // hangs off onstop, so a fake that skips it would make test 3 vacuous.
    this.ondataavailable?.({ data: new Blob(['audio']) });
    this.onstop?.();
  }
}

const tracks = { stopped: 0 };

function fakeStream(): MediaStream {
  return {
    getTracks: () => [
      {
        stop: () => {
          tracks.stopped += 1;
        },
      },
    ],
  } as unknown as MediaStream;
}

function duel(overrides: Partial<DuelSlice> = {}): DuelSlice {
  return {
    status: 'live',
    turn: 'low',
    turn_no: 1,
    turn_seconds: 90,
    low: { name: 'Ada', avatar: '🦊', value: '3' },
    high: { name: 'Grace', avatar: '🐙', value: '13' },
    recording: { host: false, low: false, high: false },
    transcript: '',
    error: '',
    mine_role: 'low',
    ...overrides,
  };
}

/** Drives the hook and exposes it, so a test can call `enable()` directly. */
function harness(initial: DuelSlice | null) {
  const seen: ReturnType<typeof useDuelMic>[] = [];
  function Probe({ slice }: { slice: DuelSlice | null }) {
    seen.push(useDuelMic(SESSION, slice));
    return null;
  }
  const view = render(<Probe slice={initial} />);
  return {
    ...view,
    mic: () => seen[seen.length - 1]!,
    setDuel: (slice: DuelSlice | null) => view.rerender(<Probe slice={slice} />),
  };
}

let uploads: { turn: string | null }[] = [];

beforeEach(() => {
  FakeRecorder.instances = [];
  tracks.stopped = 0;
  uploads = [];

  vi.stubGlobal('MediaRecorder', FakeRecorder);
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: () => Promise.resolve(fakeStream()) },
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (String(url).includes('/api/duel/audio')) {
        uploads.push({ turn: new URL(String(url), 'http://x').searchParams.get('turn') });
      }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') } as Response);
    })
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useDuelMic', () => {
  it('reports itself unusable outside a secure context', () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
    const { mic } = harness(duel());
    // Not a bug to route around: getUserMedia genuinely does not exist here, so
    // the UI has to offer the room-mic fallback instead of a dead button.
    expect(mic().capable).toBe(false);
  });

  it('records only during your own turn', async () => {
    const { mic, setDuel } = harness(duel({ turn: 'low', mine_role: 'low' }));
    await act(async () => {
      await mic().enable();
    });
    expect(FakeRecorder.instances).toHaveLength(1);
    expect(FakeRecorder.instances[0]?.started).toBe(true);

    // The floor passes to the other duelist.
    await act(async () => setDuel(duel({ turn: 'high', turn_no: 2, mine_role: 'low' })));
    expect(FakeRecorder.instances[0]?.stopped).toBe(true);
    // …and no second recorder was started for a turn that is not mine.
    expect(FakeRecorder.instances).toHaveLength(1);
  });

  it('does not record at all for a bystander', async () => {
    const { mic } = harness(duel({ mine_role: '' }));
    await act(async () => {
      await mic().enable();
    });
    expect(FakeRecorder.instances).toHaveLength(0);
  });

  it('flushes the turn and releases the mic when the floor closes mid-turn', async () => {
    const { mic, setDuel } = harness(duel({ turn: 'low', mine_role: 'low' }));
    await act(async () => {
      await mic().enable();
    });

    await act(async () => setDuel(duel({ status: 'transcribing', mine_role: 'low' })));

    expect(FakeRecorder.instances[0]?.stopped).toBe(true);
    expect(uploads).toEqual([{ turn: '1' }]);
    expect(tracks.stopped).toBe(1);
    expect(mic().armed).toBe(false);
  });

  it('releases the mic on unmount, even mid-turn', async () => {
    const { mic, unmount } = harness(duel({ turn: 'low', mine_role: 'low' }));
    await act(async () => {
      await mic().enable();
    });
    expect(tracks.stopped).toBe(0);

    unmount();
    expect(tracks.stopped).toBe(1);
  });

  it('surfaces a refused permission instead of failing silently', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: () => Promise.reject(new Error('denied')) },
    });
    const { mic } = harness(duel());
    await act(async () => {
      await mic().enable();
    });
    expect(mic().armed).toBe(false);
    expect(mic().error).toContain('the room mic covers you');
  });
});
