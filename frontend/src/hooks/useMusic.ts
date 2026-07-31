/**
 * Internet radio for the boards, as a hook.
 *
 * Ported from the identical `Music` IIFE in retro/page.py and poker/page.py.
 * The one structural change is that the `<audio>` element is owned by a ref
 * rather than created at module scope: a module-level `new Audio()` runs on
 * import, which under React's StrictMode double-mount would leave a second
 * detached element playing the same station on top of the first.
 *
 * ## Autoplay is the whole difficulty
 *
 * `audio.play()` returns a promise that browsers **reject** until the user has
 * interacted with the page. That matters here specifically because the host can
 * cast music to everyone: for a teammate who has not clicked anything yet, the
 * cast silently fails. {@link MusicApi.cast} surfaces that rejection so the
 * board can offer a "tap to hear the music" banner, which is the only thing
 * that can legally start playback for them.
 *
 * `crossOrigin = 'anonymous'` is set because these are third-party stream URLs;
 * it is what would let a real frequency-analyser read the samples. The
 * visualiser deliberately does not (see Visualizer.tsx).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface Channel {
  name: string;
  url: string;
}

export interface MusicApi {
  playing: boolean;
  channel: number;
  volume: number;
  toggle(): void;
  play(): Promise<void>;
  stop(): void;
  setChannel(index: number): void;
  setVolume(value: number): void;
  /** Apply a host command. Rejects when autoplay blocked a play request. */
  cast(index: number, on: boolean): Promise<void>;
}

const DEFAULT_VOLUME = 0.35;

export function useMusic(channels: readonly Channel[]): MusicApi {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [channel, setChannelState] = useState(0);
  const [volume, setVolumeState] = useState(DEFAULT_VOLUME);

  // Create once, on mount, and tear down on unmount. Pausing and clearing `src`
  // is not optional: an <audio> that is merely dropped keeps the stream socket
  // open and keeps playing until garbage collection eventually gets to it.
  useEffect(() => {
    const audio = new Audio();
    audio.preload = 'none';
    audio.crossOrigin = 'anonymous';
    audio.volume = DEFAULT_VOLUME;
    audioRef.current = audio;

    const onPlaying = (): void => setPlaying(true);
    const onStopped = (): void => setPlaying(false);
    audio.addEventListener('playing', onPlaying);
    audio.addEventListener('pause', onStopped);
    // A dead station is common enough that treating an error as "not playing"
    // rather than leaving the button stuck mid-buffer is the honest state.
    audio.addEventListener('error', onStopped);

    return () => {
      audio.removeEventListener('playing', onPlaying);
      audio.removeEventListener('pause', onStopped);
      audio.removeEventListener('error', onStopped);
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      audioRef.current = null;
    };
  }, []);

  const load = useCallback(
    (index: number): number => {
      if (!channels.length) return 0;
      const wrapped = ((index % channels.length) + channels.length) % channels.length;
      const audio = audioRef.current;
      const url = channels[wrapped]?.url;
      if (audio && url) audio.src = url;
      return wrapped;
    },
    [channels]
  );

  const play = useCallback(async (): Promise<void> => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.src) load(channel);
    await audio.play();
  }, [channel, load]);

  const stop = useCallback((): void => {
    audioRef.current?.pause();
  }, []);

  const api = useMemo<MusicApi>(
    () => ({
      playing,
      channel,
      volume,
      toggle() {
        if (playing) stop();
        else void play().catch(() => setPlaying(false));
      },
      play,
      stop,
      setChannel(index: number) {
        const audio = audioRef.current;
        // `!audio.paused` as well as `playing`: between clicking play and the
        // `playing` event firing there is a buffering window where the state
        // says stopped but the element is very much live. Changing station in
        // that window must keep playing, not silently drop to paused.
        const wasPlaying = playing || Boolean(audio && !audio.paused);
        const wrapped = load(index);
        setChannelState(wrapped);
        if (wasPlaying) void play().catch(() => setPlaying(false));
      },
      setVolume(value: number) {
        const clamped = Math.max(0, Math.min(1, value));
        setVolumeState(clamped);
        if (audioRef.current) audioRef.current.volume = clamped;
      },
      async cast(index: number, on: boolean) {
        if (!on) {
          stop();
          return;
        }
        const wrapped = load(index);
        setChannelState(wrapped);
        // Deliberately unguarded: the rejection *is* the signal that autoplay
        // blocked this browser, and useHostBroadcast turns it into the banner.
        await audioRef.current?.play();
      },
    }),
    [playing, channel, volume, play, stop, load]
  );

  return api;
}
