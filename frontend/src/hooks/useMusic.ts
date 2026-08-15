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
 * ## The analyser
 *
 * `crossOrigin = 'anonymous'` is what lets a real frequency analyser read the
 * samples, and the stations do send `Access-Control-Allow-Origin: *`, so the
 * visualiser is driven by the actual signal rather than a synthesised one.
 *
 * The graph is built on the first play, because an `AudioContext` created
 * before a user gesture starts suspended. Two rules it is easy to get wrong:
 * `createMediaElementSource` may be called **once** per element, and once an
 * element is routed through the graph its audio reaches the speakers *only*
 * via that graph — so failing to connect through to the destination is silence
 * with every other sign of playing.
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
  /** Live frequency data, once the graph exists. Null before the first play. */
  analyser: AnalyserNode | null;
}

const DEFAULT_VOLUME = 0.35;

export function useMusic(channels: readonly Channel[]): MusicApi {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
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
      void ctxRef.current?.close();
      ctxRef.current = null;
      sourceRef.current = null;
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

  /** Build the graph once, on a gesture. Silent failure leaves audio direct. */
  const connect = useCallback((audio: HTMLAudioElement): void => {
    if (sourceRef.current) {
      void ctxRef.current?.resume();
      return;
    }
    const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    try {
      const ctx = new Ctor();
      const source = ctx.createMediaElementSource(audio);
      const node = ctx.createAnalyser();
      node.fftSize = 128;
      node.smoothingTimeConstant = 0.75;
      source.connect(node);
      node.connect(ctx.destination);
      ctxRef.current = ctx;
      sourceRef.current = source;
      setAnalyser(node);
      void ctx.resume();
    } catch {
      // A tainted stream or a blocked context: keep plain playback rather than
      // routing the element into a graph that cannot reach the speakers.
      ctxRef.current = null;
      sourceRef.current = null;
      setAnalyser(null);
    }
  }, []);

  const play = useCallback(async (): Promise<void> => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.src) load(channel);
    connect(audio);
    await audio.play();
  }, [channel, load, connect]);

  const stop = useCallback((): void => {
    audioRef.current?.pause();
  }, []);

  const api = useMemo<MusicApi>(
    () => ({
      playing,
      channel,
      volume,
      analyser,
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
    [playing, channel, volume, analyser, play, stop, load]
  );

  return api;
}
