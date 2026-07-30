/**
 * Applying the host's broadcasts (theme, music) exactly once each.
 *
 * The host can push a palette and a radio station to every browser in the room.
 * Both arrive inside every snapshot, so the whole difficulty is *edge
 * detection*: applying them on every snapshot would yank a teammate back to the
 * host's palette a second after they picked their own, and would restart the
 * radio a few times a minute.
 *
 * Two different edges, because the two facts differ in kind:
 *
 * * **Theme** is a state. Apply it when the broadcast *value* changes. Someone
 *   who then picks their own palette keeps it until the host casts again.
 * * **Music** is a command. It carries a server-stamped `seq` and applies when
 *   that advances, so the host can re-send "play the same station" — which is
 *   the actual recovery path when a browser blocked autoplay.
 *
 * Autoplay is the reason `onMusic` returns a promise: browsers reject
 * `audio.play()` until the user has interacted with the page, and that rejection
 * is how the board knows to show the "tap to hear the music" banner instead of
 * pretending the radio is on.
 */

import { useEffect, useRef } from 'react';

import { isTheme, type Theme } from '../runtime/theme';

export interface MusicCommand {
  playing: boolean;
  channel: number;
  seq: number;
}

export interface BroadcastState {
  theme?: string | null;
  music?: MusicCommand | null;
}

export interface HostBroadcastHandlers {
  onTheme(theme: Theme): void;
  /** Resolve when the radio actually started; reject when autoplay blocked it. */
  onMusic(command: MusicCommand): Promise<void>;
  /** Called with `true` when a play command was blocked by autoplay policy. */
  onAutoplayBlocked?(blocked: boolean): void;
}

export function useHostBroadcast(
  broadcast: BroadcastState | null | undefined,
  { onTheme, onMusic, onAutoplayBlocked }: HostBroadcastHandlers
): void {
  // High-water marks. Refs, not state: crossing an edge must not itself cause a
  // render, and these must survive re-renders without resetting.
  const lastTheme = useRef<string | null>(null);
  const lastSeq = useRef(0);

  const handlers = useRef({ onTheme, onMusic, onAutoplayBlocked });
  handlers.current = { onTheme, onMusic, onAutoplayBlocked };

  const theme = broadcast?.theme ?? null;
  const music = broadcast?.music ?? null;
  const seq = music?.seq ?? 0;

  useEffect(() => {
    if (!theme || theme === lastTheme.current) return;
    lastTheme.current = theme;
    // Validate rather than trust: the value is server-checked against
    // RETRO_THEMES, but a client that writes an unknown palette onto <html>
    // renders unstyled, and failing closed here costs nothing.
    if (isTheme(theme)) handlers.current.onTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (!music || seq <= lastSeq.current) return;
    lastSeq.current = seq;
    handlers.current
      .onMusic(music)
      .then(() => handlers.current.onAutoplayBlocked?.(false))
      .catch(() => handlers.current.onAutoplayBlocked?.(music.playing));
    // `music` is a freshly-parsed object on every snapshot, so this effect
    // re-runs constantly — the `seq <= lastSeq` guard above is what makes that
    // harmless, rather than an incomplete dependency list.
  }, [seq, music]);
}
