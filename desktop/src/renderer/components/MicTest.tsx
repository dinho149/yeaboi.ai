// Settings → Voice Input: pick a microphone and prove it is being heard.
//
// The device list is this engine's, not PortAudio's — the window is what opens
// the microphone here, so the terminal's list would be answering a question
// about a different machine's audio stack. What is shared is the *preference*:
// VOICE_DEVICE holds a name, and each surface resolves that name against the
// list it actually has.
//
// Labels are blank until the OS permission has been granted once, so the picker
// asks for the microphone before it can name one. That is the same prompt
// dictation would raise on first use, moved somewhere it can be explained.

import { useEffect, useRef, useState } from 'react';
import { type MicDevice, MicSession, listMics, meterCells, pickMic } from '../voice';

export interface MicTestProps {
  /** The saved VOICE_DEVICE name. */
  value: string;
  onSave: (name: string) => void;
}

const METER_MS = 100;

export function MicTest({ value, onSave }: MicTestProps) {
  const [devices, setDevices] = useState<MicDevice[]>([]);
  const [testing, setTesting] = useState(false);
  const [level, setLevel] = useState(0);
  const [note, setNote] = useState('');
  const session = useRef<MicSession | null>(null);

  useEffect(() => {
    void refresh();
    return () => session.current?.close();
  }, []);

  useEffect(() => {
    if (!testing) return;
    const timer = window.setInterval(() => setLevel(session.current?.level() ?? 0), METER_MS);
    return () => window.clearInterval(timer);
  }, [testing]);

  async function refresh(): Promise<void> {
    try {
      setDevices(await listMics());
    } catch (error) {
      setNote((error as Error).message);
    }
  }

  async function start(): Promise<void> {
    setNote('');
    try {
      session.current = await MicSession.open(pickMic(devices, value), { record: false });
    } catch (error) {
      setNote(`Microphone unavailable — ${(error as Error).message}`);
      return;
    }
    setTesting(true);
    // Permission granted means labels exist now; a first-run list was blank.
    void refresh();
  }

  function stop(): void {
    session.current?.close();
    session.current = null;
    setTesting(false);
    setLevel(0);
  }

  const named = devices.some((device) => device.label);
  const cells = meterCells(level);

  return (
    <div class="settings-row mic-test">
      <span class="settings-label">Microphone</span>
      <select value={value} onChange={(event) => onSave((event.target as HTMLSelectElement).value)}>
        <option value="">system default</option>
        {devices
          .filter((device) => device.label)
          .map((device) => (
            <option key={device.deviceId} value={device.label}>
              {device.label}
            </option>
          ))}
        {value && !devices.some((device) => device.label === value) && (
          <option value={value}>{value} (saved)</option>
        )}
      </select>
      <button type="button" onClick={() => (testing ? stop() : void start())}>
        {testing ? 'Stop' : named ? 'Test' : 'Allow microphone'}
      </button>
      {testing && (
        <span class="mic-meter" aria-label={`input level ${cells} of 8`}>
          {'▇'.repeat(cells) + '▁'.repeat(8 - cells)}
        </span>
      )}
      {testing && cells === 0 && <span class="mic-status">say something — nothing is reaching this mic</span>}
      {note && <span class="mic-status error">{note}</span>}
    </div>
  );
}
