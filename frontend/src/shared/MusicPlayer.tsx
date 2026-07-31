/**
 * The music popover's contents: play/pause, station, volume.
 *
 * The station picker is a real `<select>` with a `<label>`, and the volume a
 * real `<input type="range">`. Both were unlabelled controls before, which on a
 * screen reader read as "combo box" and "slider" with no indication of what
 * either governed.
 */

import { useId, type ReactNode } from 'react';

import type { Channel, MusicApi } from '../hooks/useMusic';
import { cx } from '../runtime/cx';
import styles from './shared.module.css';

export interface MusicPlayerProps {
  music: MusicApi;
  channels: readonly Channel[];
  /** Rendered at the bottom — the host's "cast to everyone" button. */
  footer?: ReactNode;
  className?: string | undefined;
}

export function MusicPlayer({ music, channels, footer, className }: MusicPlayerProps) {
  const stationId = useId();
  const volumeId = useId();

  return (
    <div className={cx(styles['musicPanel'], className)}>
      <div className={styles['musicRow']}>
        <button
          type="button"
          className={styles['musicPlay']}
          onClick={() => music.toggle()}
          aria-label={music.playing ? 'Pause music' : 'Play music'}
        >
          <span aria-hidden="true">{music.playing ? '⏸' : '▶'}</span>
        </button>

        <label className={styles['fieldLabel']} htmlFor={stationId}>
          Station
        </label>
        <select
          id={stationId}
          className={styles['select']}
          value={String(music.channel)}
          onChange={(event) => music.setChannel(Number((event.target as HTMLSelectElement).value))}
        >
          {channels.map((channel, index) => (
            <option key={channel.url} value={String(index)}>
              {channel.name}
            </option>
          ))}
        </select>
      </div>

      <div className={styles['musicRow']}>
        <label className={styles['fieldLabel']} htmlFor={volumeId}>
          Volume
        </label>
        <input
          id={volumeId}
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={String(music.volume)}
          className={styles['range']}
          // Percent, not the raw 0–1 float: "zero point three five" is not a
          // volume anyone recognises being read out.
          aria-valuetext={`${Math.round(music.volume * 100)} percent`}
          onInput={(event) => music.setVolume(Number((event.target as HTMLInputElement).value))}
        />
      </div>

      {footer}
    </div>
  );
}
