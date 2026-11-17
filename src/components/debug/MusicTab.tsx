/**
 * A scrolling scope for the music side channel: what it is hearing, right now.
 *
 * The measurements behind this feature are equivocal — the chord stream is
 * worth about +1% to the matcher at this detector's accuracy, which is the
 * break-even point (docs/research/CHORD_DOWNBEAT.md). Numbers that close cannot settle
 * whether the detector is nearly right or badly wrong, and the recordings
 * cannot show *why*. So this panel is deliberately literal: the waveform, the
 * chord over it, the beats on it, and the three candidates being chosen
 * between, with no smoothing beyond what the analyser itself applies.
 */

import { useEffect, useRef } from "react";

import { useToggleRequest } from "@/components/debug/useToggleRequest";
import {
  LEVEL_HZ,
  WINDOW_S,
  displayDelay,
  setMusicAnalysis,
  useMusicAnalysis,
  type MusicState,
} from "@/lib/musicAnalysis";

export const ROOTS = [
  "C", "C#", "D", "D#", "E", "F",
  "F#", "G", "G#", "A", "A#", "B",
];

/** One hue per root, minors darker — so a chord change is visible at a glance. */
function chordColour(name: string, alpha = 1): string {
  const minor = name.endsWith("m");
  const root = minor ? name.slice(0, -1) : name;
  const idx = ROOTS.indexOf(root);
  if (idx < 0) return `rgba(160,160,170,${alpha})`;
  const hue = (idx * 30) % 360;
  return `hsla(${hue}, ${minor ? 45 : 70}%, ${minor ? 45 : 58}%, ${alpha})`;
}

/**
 * Exported for test. The time-to-pixel mapping is the part of this panel most
 * able to be confidently wrong — a chord band or a beat drawn at the wrong x
 * looks entirely plausible, and would have us reading the analyser's timing
 * through a broken lens.
 */
export function draw(
  canvas: HTMLCanvasElement,
  s: MusicState,
  viewEnd: number = s.tEnd,
): void {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  if (cssW === 0 || cssH === 0) return;
  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const bandH = 18;
  const waveTop = bandH + 2;
  const waveH = cssH - waveTop;
  const mid = waveTop + waveH / 2;
  const t0 = viewEnd - WINDOW_S;
  const xOf = (t: number) => ((t - t0) / WINDOW_S) * cssW;

  ctx.fillStyle = "rgba(255,255,255,0.03)";
  ctx.fillRect(0, 0, cssW, cssH);

  // Second gridlines, so the eye can measure how long a chord lasted.
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  for (let k = Math.ceil(t0); k < s.tEnd; k += 1) {
    const x = Math.round(xOf(k)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, waveTop);
    ctx.lineTo(x, cssH);
    ctx.stroke();
  }

  // Chord bands along the top.
  for (const seg of s.segments) {
    const x1 = Math.max(0, xOf(seg.tStart));
    const x2 = Math.min(cssW, xOf(seg.tEnd));
    if (x2 <= x1) continue;
    ctx.fillStyle = chordColour(seg.name, 0.35);
    ctx.fillRect(x1, 0, x2 - x1, bandH);
    ctx.fillStyle = chordColour(seg.name, 0.18);
    ctx.fillRect(x1, waveTop, x2 - x1, waveH);
    if (x2 - x1 > 20) {
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillText(seg.name, x1 + 4, bandH / 2);
    }
  }

  // Waveform: RMS mirrored about the centre line.
  const n = s.levels.length;
  if (n > 0) {
    const peak = Math.max(0.02, ...s.levels);
    const newestT = s.tEnd;
    ctx.fillStyle = "rgba(226,232,240,0.85)";
    for (let i = 0; i < n; i += 1) {
      const level = s.levels[i] ?? 0;
      const t = newestT - (n - 1 - i) / LEVEL_HZ;
      const x = xOf(t);
      if (x < 0 || x > cssW) continue;
      const h = Math.max(1, (level / peak) * (waveH / 2) * 0.92);
      ctx.fillRect(x, mid - h, Math.max(1, cssW / (WINDOW_S * LEVEL_HZ)), h * 2);
    }
  }

  // Beats.
  for (const b of s.beats) {
    const x = Math.round(xOf(b)) + 0.5;
    if (x < 0 || x > cssW) continue;
    ctx.strokeStyle = "rgba(52,211,153,0.85)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, waveTop);
    ctx.lineTo(x, cssH);
    ctx.stroke();
    ctx.fillStyle = "rgba(52,211,153,0.95)";
    ctx.beginPath();
    ctx.arc(x, waveTop + 4, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // The "now" edge. Beats are placed slightly in the past by construction —
  // one cannot be confirmed until the onset envelope has passed it — so the
  // rightmost region is expected to be empty of beats.
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cssW - 0.5, 0);
  ctx.lineTo(cssW - 0.5, cssH);
  ctx.stroke();
}

export function MusicTab() {
  const s = useMusicAnalysis();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // The render loop reads state through a ref so it is not restarted on every
  // message; restarting it ten times a second is what a naive `useEffect([s])`
  // would do, and it would reintroduce the stutter this exists to remove.
  const stateRef = useRef(s);
  stateRef.current = s;
  const onChords = useToggleRequest("chord detection", (on) =>
    setMusicAnalysis(on, s.beatsEnabled),
  );
  const onBeats = useToggleRequest("beat detection", (on) =>
    setMusicAnalysis(s.chordsEnabled, on),
  );

  useEffect(() => {
    let raf = 0;
    // Audio time the right-hand edge is showing, advanced by the wall clock.
    let viewEnd: number | null = null;
    let lastWall = 0;
    // When the newest audio time last moved, so a stopped session parks the
    // scope instead of scrolling off into empty space.
    let lastAdvance = 0;
    let lastSeen = 0;

    const tick = (nowMs: number) => {
      raf = requestAnimationFrame(tick);
      const canvas = canvasRef.current;
      const state = stateRef.current;
      if (!canvas || !state.seen) return;
      const now = nowMs / 1000;
      if (state.tEnd !== lastSeen) {
        lastSeen = state.tEnd;
        lastAdvance = now;
      }
      const target = state.tEnd - displayDelay(state.arrivalJitter);
      if (viewEnd === null || Math.abs(target - viewEnd) > 3) {
        // First frame, or the timeline jumped (a new session).
        viewEnd = target;
      } else if (now - lastAdvance < 1.0) {
        const dt = Math.min(0.25, now - lastWall);
        // Advance with real time, then ease toward the target. Easing rather
        // than snapping is the whole point: snapping to each burst is exactly
        // the once-a-second jerk being removed.
        viewEnd += dt + (target - viewEnd) * Math.min(1, dt * 3);
      }
      lastWall = now;
      draw(canvas, state, viewEnd);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const on = s.chordsEnabled || s.beatsEnabled;

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 text-xs">
      <div className="mb-3 flex gap-2">
        <label className="flex flex-1 items-start gap-2 rounded border border-white/10 bg-white/5 px-2 py-2">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.chordsEnabled}
            onChange={(e) => onChords(e.target.checked)}
          />
          <span className="font-sans leading-tight">
            <span className="text-zinc-200">Chords</span>
            <span className="block text-[10px] text-zinc-400">
              What harmony is sounding now.
            </span>
          </span>
        </label>
        <label className="flex flex-1 items-start gap-2 rounded border border-white/10 bg-white/5 px-2 py-2">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.beatsEnabled}
            onChange={(e) => onBeats(e.target.checked)}
          />
          <span className="font-sans leading-tight">
            <span className="text-zinc-200">Beats</span>
            <span className="block text-[10px] text-zinc-400">
              Tempo and beat grid.
            </span>
          </span>
        </label>
      </div>

      {!on ? (
        <p className="font-sans text-zinc-400">
          Both are off. They analyse the live microphone alongside the main
          listener and drive nothing — switch one on and start a session to see
          what the room sounds like to the engine.
        </p>
      ) : !s.seen ? (
        <p className="font-sans text-zinc-400">
          Waiting for audio. Start a session.
        </p>
      ) : (
        <>
          <div className="mb-2 flex items-center gap-4 font-sans text-[11px] text-zinc-400">
            <span>
              Now:{" "}
              <span className="font-mono text-base text-zinc-100">
                {s.chord ?? "—"}
              </span>
            </span>
            <span>
              Tempo:{" "}
              <span className="font-mono text-zinc-200">
                {s.bpm > 0 ? `${s.bpm.toFixed(0)} BPM` : "—"}
              </span>
            </span>
            <span
              className={
                s.backend === "btc" ? "text-emerald-400" : "text-amber-400"
              }
              title={
                s.backend === "btc"
                  ? "BTC, a learned recogniser — about twice as accurate"
                  : `Numpy template matching. Learned model: ${s.backendStatus}`
              }
            >
              {s.backend === "btc" ? "BTC" : "templates"}
            </span>
            <span className="ml-auto" title={
              s.arrivalJitter > 0.4
                ? `Updates arrive in bursts up to ${s.arrivalJitter.toFixed(1)}s ` +
                  "apart because the ASR pass blocks the capture thread; the " +
                  "scope draws that far behind live so the scroll stays smooth."
                : "Analysis cost, as a share of one core."
            }>
              {(s.realtimeFactor * 100).toFixed(1)}% of one core
              {s.arrivalJitter > 0.4
                ? ` · ${displayDelay(s.arrivalJitter).toFixed(1)}s behind`
                : ""}
            </span>
          </div>

          <canvas
            ref={canvasRef}
            className="mb-3 h-40 w-full rounded border border-white/10"
          />

          <div className="mb-1 font-sans text-[11px] text-zinc-400">
            Most likely right now
          </div>
          <div className="space-y-1">
            {s.candidates.length === 0 ? (
              <p className="font-sans text-[11px] text-zinc-500">
                Chord detection is off.
              </p>
            ) : (
              s.candidates.map((c, i) => (
                <div key={c.name} className="flex items-center gap-2">
                  <span
                    className="w-10 font-mono text-zinc-200"
                    style={{ opacity: i === 0 ? 1 : 0.65 }}
                  >
                    {c.name}
                  </span>
                  <div className="h-3 flex-1 overflow-hidden rounded bg-white/5">
                    <div
                      className="h-full"
                      style={{
                        width: `${Math.round(c.confidence * 100)}%`,
                        background: chordColour(c.name, 0.8),
                      }}
                    />
                  </div>
                  <span className="w-10 text-right font-mono text-[10px] text-zinc-400">
                    {(c.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              ))
            )}
          </div>

          <p className="mt-3 font-sans text-[10px] leading-snug text-zinc-500">
            {s.backend === "btc" ? (
              <>
                Chords come from BTC, a learned recogniser. It reads a
                ten-second block and the newest moment is the one it is least
                sure of, so the right-hand edge is its weakest.
              </>
            ) : (
              <>
                Chords come from template matching, which is about half as
                accurate as the learned model. Install it with{" "}
                <code className="text-zinc-400">
                  scripts/setup_chord_model.py
                </code>
                .
              </>
            )}{" "}
            Confidence shows which candidates are close, not how often the top
            one is right. Beats sit slightly left of the live edge because a
            beat cannot be confirmed until the audio has passed it.
          </p>
        </>
      )}
    </div>
  );
}
