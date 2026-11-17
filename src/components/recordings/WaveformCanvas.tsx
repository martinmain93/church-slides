/**
 * Renders a precomputed min/max peak array as a waveform. Resizes to its
 * container and redraws on width change. Pure presentation — no audio access.
 */

import { useEffect, useRef } from "react";

export interface Peaks {
  /** Normalized min sample per bucket, in [-1, 0]. */
  min: Float32Array;
  /** Normalized max sample per bucket, in [0, 1]. */
  max: Float32Array;
}

/**
 * `startFrac`/`endFrac` select the slice of the recording to draw, as
 * fractions of its full length — that's how the editor's zoom works. The
 * default (0 → 1) draws everything.
 */
export function WaveformCanvas({
  peaks,
  startFrac = 0,
  endFrac = 1,
}: {
  peaks: Peaks | null;
  startFrac?: number;
  endFrac?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const draw = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = window.devicePixelRatio || 1;
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.max(1, Math.floor(h * dpr));
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);
      const mid = h / 2;

      // Baseline
      ctx.strokeStyle = "rgba(148, 163, 184, 0.25)"; // slate-400/25
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      if (!peaks || peaks.max.length === 0) return;

      const n = peaks.max.length;
      const lo = Math.min(Math.max(startFrac, 0), 1) * n;
      const hi = Math.min(Math.max(endFrac, 0), 1) * n;
      const visible = Math.max(hi - lo, 1e-9);

      ctx.fillStyle = "rgba(99, 102, 241, 0.55)"; // indigo-500/55
      for (let x = 0; x < w; x++) {
        // Each pixel covers a range of buckets — at zoom 1 that's several, and
        // zoomed in it's a fraction of one. Take the extremes across the range
        // so transients survive downsampling instead of being point-sampled.
        const from = lo + (x / w) * visible;
        const to = lo + ((x + 1) / w) * visible;
        const b0 = Math.min(n - 1, Math.max(0, Math.floor(from)));
        const b1 = Math.min(n - 1, Math.max(b0, Math.ceil(to) - 1));
        let hiV = 0;
        let loV = 0;
        for (let b = b0; b <= b1; b++) {
          const mx = peaks.max[b] ?? 0;
          const mn = peaks.min[b] ?? 0;
          if (mx > hiV) hiV = mx;
          if (mn < loV) loV = mn;
        }
        const top = mid - hiV * mid;
        const bottom = mid - loV * mid;
        ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
      }
    };

    draw();
    const ro = new ResizeObserver(draw);
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    return () => ro.disconnect();
  }, [peaks, startFrac, endFrac]);

  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />;
}

/**
 * Downsample a decoded mono channel into a fixed number of min/max buckets
 * for waveform rendering. Buckets are independent of the eventual pixel
 * width — the canvas stretches them.
 *
 * The count is set for the deepest zoom rather than for a full-length view:
 * at MAX_ZOOM only 1/64th of these buckets spans the canvas, so a coarse
 * array would render as visible steps. 8000 floats is ~64 KB — cheap.
 */
export function computePeaks(channel: Float32Array, buckets = 8000): Peaks {
  const min = new Float32Array(buckets);
  const max = new Float32Array(buckets);
  if (channel.length === 0) return { min, max };
  const step = channel.length / buckets;
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * step);
    const end = Math.min(channel.length, Math.floor((b + 1) * step));
    let lo = 0;
    let hi = 0;
    for (let i = start; i < end; i++) {
      const v = channel[i] ?? 0;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    min[b] = lo;
    max[b] = hi;
  }
  return { min, max };
}
