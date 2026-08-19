/**
 * The little bar visualiser beside the music button.
 *
 * Driven by a real `AnalyserNode` when `useMusic` has one — the stations do
 * send `Access-Control-Allow-Origin: *`, so the samples are readable. That
 * makes it honest: a station playing silence draws flat, which is the whole
 * point of having it. Two summed sines per bar remain as the fallback for the
 * window before the graph exists, or a browser without one.
 *
 * `aria-hidden`: it says the same thing the play button already says.
 *
 * The rAF loop stops when `playing` goes false and on unmount — an animation
 * frame loop left running against a detached canvas is a genuine battery drain
 * on the phones these boards live on.
 */

import { useEffect, useRef } from 'react';

import { cx } from '../runtime/cx';
import styles from './shared.module.css';

const BARS = 16;
const SPEED = 0.18;

export interface VisualizerProps {
  playing: boolean;
  /** Live signal. Falls back to the synthetic bars when absent. */
  analyser?: AnalyserNode | null;
  width?: number;
  height?: number;
  className?: string | undefined;
}

export function Visualizer({ playing, analyser, width = 34, height = 22, className }: VisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !playing) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reduced =
      typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const barWidth = canvas.width / BARS;
    const bins = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
    let phase = 0;
    let raf = 0;

    /** 0..1 per bar: the real spectrum, or the synthesised stand-in. */
    const levels = (): number[] => {
      if (analyser && bins) {
        analyser.getByteFrequencyData(bins);
        // The top of the range is mostly empty on a 128kbps stream, so the bars
        // read the lower half of the spectrum and spread it across the width.
        const usable = Math.floor(bins.length / 2);
        const per = Math.max(1, Math.floor(usable / BARS));
        return Array.from({ length: BARS }, (_, i) => {
          let sum = 0;
          for (let k = 0; k < per; k += 1) sum += bins[i * per + k] ?? 0;
          return sum / per / 255;
        });
      }
      phase += SPEED;
      return Array.from({ length: BARS }, (_, i) => (Math.sin(phase + i * 0.7) + Math.sin(phase * 1.7 + i) + 2) / 4);
    };

    const frame = (): void => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      // Read the accent each frame rather than caching it: the host can cast a
      // new theme mid-song, and a cached colour would keep the old palette.
      const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
      ctx.fillStyle = accent || '#5ac88a';
      levels().forEach((v, i) => {
        const h = Math.max(1, v * canvas.height);
        ctx.fillRect(i * barWidth, canvas.height - h, barWidth - 1, h);
      });
      raf = requestAnimationFrame(frame);
    };

    if (reduced) {
      // Still show that music is on — one static frame, no animation.
      frame();
      cancelAnimationFrame(raf);
    } else {
      raf = requestAnimationFrame(frame);
    }

    return () => {
      cancelAnimationFrame(raf);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [playing, analyser]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      aria-hidden="true"
      className={cx(styles['viz'], playing && styles['vizOn'], className)}
    />
  );
}
