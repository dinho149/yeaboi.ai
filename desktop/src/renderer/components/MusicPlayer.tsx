// The sidebar music player.
//
// The terminal shells out to `ffplay` because it has no way to make a sound
// itself; a browser engine does, so this is an `<audio>` element and the
// desktop needs no ffmpeg at all. The catalogue is the same one the terminal
// plays — served by /api/ambience — and the on/off and channel choices are
// persisted through the same preferences, so the two surfaces agree about what
// was left playing.
//
// Nothing auto-plays. Music starts when someone asks for it: the persisted
// preference records the choice, never a licence to make noise on launch.

import { useEffect, useRef, useState } from 'react';
import { type MusicChannel, setAmbience } from '../ambience';

export interface MusicPlayerProps {
  channels: MusicChannel[];
  channel: number;
  /** Told when playback starts or stops, so the duck can dance to it. */
  onPlaying?: (playing: boolean) => void;
}

export function MusicPlayer({ channels, channel, onPlaying }: MusicPlayerProps) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [index, setIndex] = useState(channel);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState('');

  const station = channels[index];

  useEffect(() => {
    onPlaying?.(playing);
  }, [playing, onPlaying]);

  if (channels.length === 0 || !station) return null;

  function toggle(): void {
    const element = audio.current;
    if (!element) return;
    if (playing) {
      element.pause();
      setPlaying(false);
      void setAmbience({ music_enabled: false });
      return;
    }
    setError('');
    element.play().then(
      () => {
        setPlaying(true);
        void setAmbience({ music_enabled: true });
      },
      // A station can be unreachable or blocked; saying so beats an equalizer
      // animating over silence, which is what the terminal's bar had to learn.
      (e: Error) => setError(e.message || 'the station would not start'),
    );
  }

  function cycle(): void {
    const next = (index + 1) % channels.length;
    setIndex(next);
    void setAmbience({ music_channel: next });
    if (playing) {
      // The element reloads on the new src; play again once it has one.
      window.setTimeout(() => void audio.current?.play().catch(() => setPlaying(false)), 0);
    }
  }

  return (
    <div class="music-player">
      <audio ref={audio} src={station.url} preload="none" onEnded={() => setPlaying(false)} />
      <button type="button" class="music-toggle" onClick={toggle} aria-pressed={playing}>
        {playing ? '❙❙' : '▶'}
      </button>
      <button type="button" class="music-channel" onClick={cycle} title="Next station">
        {station.name}
      </button>
      {error && <span class="music-error">{error}</span>}
    </div>
  );
}
