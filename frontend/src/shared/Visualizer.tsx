/**
 * The little bar visualiser beside the music button.
 *
 * It is **synthetic**, not a frequency analysis — two summed sines per bar, the
 * same trick the original board script used. Worth being explicit about,
 * because the honest-looking alternative does not work: routing the audio
 * through an `AnalyserNode` requires a CORS-permissive stream, and the public
 * radio stations these pages use do not send the header. You get an analyser
 * full of silence, or a tainted node that refuses to read at all.
 *
 * So it is decoration that says "sound is happening", which is exactly what the
 * toolbar needs it to say. `aria-hidden`, accordingly.
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
  width?: number;
  height?: number;
  className?: string | undefined;
}

export function Visualizer({ playing, width = 34, height = 22, className }: VisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !playing) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reduced =
      typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const barWidth = canvas.width / BARS;
    let phase = 0;
    let raf = 0;

    const frame = (): void => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      // Read the accent each frame rather than caching it: the host can cast a
      // new theme mid-song, and a cached colour would keep the old palette.
      const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
      ctx.fillStyle = accent || '#5ac88a';
      phase += SPEED;
      for (let i = 0; i < BARS; i += 1) {
        const v = (Math.sin(phase + i * 0.7) + Math.sin(phase * 1.7 + i) + 2) / 4;
        const h = Math.max(2, v * canvas.height);
        ctx.fillRect(i * barWidth, canvas.height - h, barWidth - 1, h);
      }
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
  }, [playing]);

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
