/**
 * The music popover's contents: play/pause, station, volume.
 *
 * One field per row, each a label above its control — the same grammar the
 * timer and theme panels use, so the three read as one set rather than three
 * layouts that happen to share a notch. The station is the {@link Dropdown}
 * primitive rather than a `<select>`, which draws the operating system's own
 * widget and can be typeset with nothing else on the page.
 */

import type { ReactNode } from 'react';

import type { Channel, MusicApi } from '../hooks/useMusic';
import { cx } from '../runtime/cx';
import styles from './shared.module.css';
import { Dropdown, Icon } from '../design/primitives';

export interface MusicPlayerProps {
  music: MusicApi;
  channels: readonly Channel[];
  /** Rendered at the bottom — the host's "cast to everyone" button. */
  footer?: ReactNode;
  className?: string | undefined;
}

export function MusicPlayer({ music, channels, footer, className }: MusicPlayerProps) {
  const names = channels.map((channel) => channel.name);
  const current = channels[music.channel]?.name ?? names[0] ?? '';

  return (
    <div className={cx(styles['panelForm'], className)}>
      <div className={styles['musicHead']}>
        <button
          type="button"
          className={styles['musicPlay']}
          onClick={() => music.toggle()}
          aria-label={music.playing ? 'Pause music' : 'Play music'}
        >
          <Icon name={music.playing ? 'pause' : 'play'} size={18} />
        </button>
        <div className={styles['field']}>
          <span className={styles['fieldLabel']}>Station</span>
          <Dropdown
            label="Station"
            value={current}
            options={names}
            onChange={(name) => {
              const index = names.indexOf(name);
              if (index >= 0) music.setChannel(index);
            }}
          />
        </div>
      </div>

      <div className={styles['field']}>
        <span className={styles['fieldLabel']}>Volume</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={String(music.volume)}
          className={styles['range']}
          aria-label="Volume"
          // Percent, not the raw 0–1 float: "zero point three five" is not a
          // volume anyone recognises being read out.
          aria-valuetext={`${Math.round(music.volume * 100)} percent`}
          // The filled part of the track is painted from this, so the slider
          // shows its value rather than only its handle position.
          style={{ '--fill': `${music.volume * 100}%` } as never}
          onInput={(event) => music.setVolume(Number((event.target as HTMLInputElement).value))}
        />
      </div>

      {footer}
    </div>
  );
}
