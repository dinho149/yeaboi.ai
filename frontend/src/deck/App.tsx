/**
 * The slide deck.
 *
 * A deck is the simplest surface yeaboi ships: no server, no polling, no
 * mutation. One index, a keymap, and a palette. Everything interesting about it
 * happened on the Python side, where the slides were composed.
 *
 * Two behaviours are worth knowing about before editing:
 *
 * * **The slide remounts on every change** (`key={index}`). That is what
 *   replays the enter animation — a CSS animation on a node React merely
 *   updates does not restart, and a deck whose slides do not visibly change is
 *   disorienting when you are clicking through it from the back of a room.
 * * **Style colours resolve per palette, not once.** The old renderer baked
 *   them into a stylesheet at export time, so pressing T re-themed the whole
 *   deck except the heading colour the user had picked.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { useDuckPulse } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { DeckBoot, DeckPalette } from './boot';
import { Brand, Controls } from './Chrome';
import { applyPalette, nextTheme, resolveColor } from './palette';
import { Slide } from './Slide';
import styles from './deck.module.css';

/** Keys that move the deck, and by how much. */
const STEP: Record<string, number> = {
  ArrowRight: 1,
  ' ': 1,
  PageDown: 1,
  ArrowLeft: -1,
  PageUp: -1,
};

function toggleFullscreen(): void {
  if (document.fullscreenElement) void document.exitFullscreen();
  else void document.documentElement.requestFullscreen();
}

export function App({ boot }: { boot: DeckBoot }) {
  const { slides, style } = boot;
  const total = Math.max(slides.length, 1);
  const [index, setIndex] = useState(0);
  const [theme, setTheme] = useState(boot.theme);

  const names = useMemo(() => Object.keys(boot.palettes), [boot.palettes]);
  // A payload always carries the palette it names, but this is a file that can
  // outlive the exporter that wrote it — falling back beats rendering unstyled.
  const palette = (boot.palettes[theme] ?? boot.palettes[names[0] ?? '']) as DeckPalette | undefined;

  useEffect(() => {
    if (palette) applyPalette(theme, palette);
  }, [theme, palette]);

  const go = useCallback(
    (by: number) => setIndex((at) => Math.max(0, Math.min(total - 1, at + by))),
    [total]
  );
  const cycle = useCallback(() => setTheme((at) => nextTheme(names, at)), [names]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const step = STEP[event.key];
      if (step !== undefined) {
        event.preventDefault();
        go(step);
      } else if (event.key === 'Home') setIndex(0);
      else if (event.key === 'End') setIndex(total - 1);
      else if (event.key === 't' || event.key === 'T') cycle();
      else if (event.key === 'f' || event.key === 'F') toggleFullscreen();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [go, cycle, total]);

  // One flap when the deck lands on its last slide. The duck is a live
  // indicator on the boards; a deck has exactly one moment worth marking, and
  // scattering reactions across every arrow press would make it wallpaper.
  const [duck, flap] = useDuckPulse('idle');
  useEffect(() => {
    if (index === total - 1 && total > 1) flap('joined');
  }, [index, total, flap]);

  const vars: Record<string, string> = {
    '--deck-scale': String(style.fontScale || 1),
    '--deck-font': style.fontFamily,
  };
  if (palette) {
    const title = resolveColor(style.titleColor, palette);
    const heading = resolveColor(style.headingColor, palette);
    if (title) vars['--deck-title'] = title;
    if (heading) vars['--deck-heading'] = heading;
  }

  const slide = slides[index];

  return (
    <div className={styles['app']} style={vars}>
      <p className={styles['rail']}>{boot.project}</p>
      <Brand generated={boot.generated} duck={duck} />

      {/* The whole slide is the live region: on a deck, "what changed" and
          "what is on screen" are the same thing. */}
      <main className={styles['stage']} aria-live="polite">
        {slide ? <Slide key={index} slide={slide} period={boot.period} /> : null}
      </main>

      <Controls
        index={index}
        total={total}
        onPrev={() => go(-1)}
        onNext={() => go(1)}
        onTheme={cycle}
        nextThemeName={nextTheme(names, theme)}
      />

      {style.footer ? <p className={cx(styles['corner'], styles['cornerLeft'])}>{style.footer}</p> : null}
      {style.slideNumbers ? (
        <p className={cx(styles['corner'], styles['cornerRight'])}>{index + 1}</p>
      ) : null}
    </div>
  );
}
