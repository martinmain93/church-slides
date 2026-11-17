/**
 * Music panel under the model inspector's waveform.
 *
 * Same time window as the scrubber: zoom and playhead are the parent's.
 * Rows, top to bottom: a pulse that is allowed to move, Beat This beats,
 * chords, Basic Pitch, and multi-band onsets. A row opens upward for the
 * numbers behind the drawing. An open onset band can be soloed in the audio.
 */

import { memo, useEffect, useMemo, useRef, useState, type Ref, type RefObject } from "react";

import {
  timeToViewFraction,
  viewFractionToTime,
  type ViewWindow,
} from "@/lib/recordings/timeline";
import type { InspectMusicResponse } from "@/types/generated";

const ROW = {
  pulse: 36,
  beats: 22,
  chords: 22,
  pitch: 78,
  onset: 16,
} as const;

const EXPANDED = {
  pulse: 100,
  beats: 64,
  chords: 48,
  pitch: 220,
  onset: 64,
} as const;

const BAND_COLOR: Record<string, string> = {
  kick: "#f87171",
  bass: "#fb923c",
  body: "#facc15",
  strum: "#34d399",
  air: "#38bdf8",
  drums: "#f87171",
  other: "#facc15",
  vocals: "#c4b5fd",
};

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

/** Display ranges. The floors are what the analysis stores; the defaults
 *  are the cuts the line used before the sliders existed. */
export const PITCH_LIMITS = {
  contour: { min: 0.05, max: 0.95, step: 0.01, default: 0.2 },
  note: { min: 0.15, max: 0.95, step: 0.01, default: 0.5 },
  minNote: { min: 0.04, max: 0.5, step: 0.01, default: 0.08 },
  maxJump: { min: 1, max: 24, step: 1, default: 7 },
  maxGap: { min: 0.05, max: 1, step: 0.01, default: 0.2 },
} as const;

export type PitchParams = {
  contour: number;
  note: number;
  minNote: number;
  maxJump: number;
  maxGap: number;
};

export const DEFAULT_PITCH_PARAMS: PitchParams = {
  contour: PITCH_LIMITS.contour.default,
  note: PITCH_LIMITS.note.default,
  minNote: PITCH_LIMITS.minNote.default,
  maxJump: PITCH_LIMITS.maxJump.default,
  maxGap: PITCH_LIMITS.maxGap.default,
};

type PitchPoint = { t_seconds: number; midi: number; confidence: number; heard?: number };
type NoteSpan = {
  t_start: number;
  t_end: number;
  midi: number;
  confidence: number;
  heard?: number;
};

/** Hide stored pitch the sliders have cut. The model is not run again. */
export function visiblePitch(pitch: PitchPoint[], notes: NoteSpan[], params: PitchParams) {
  return {
    pitch: pitch.filter((p) => p.confidence >= params.contour),
    notes: notes.filter(
      (n) => n.confidence >= params.note && n.t_end - n.t_start >= params.minNote,
    ),
  };
}

export const MELODY_LIMITS = {
  window: { min: 0.04, max: 0.4, step: 0.01, default: 0.12 },
  strength: { min: 0, max: 1, step: 0.05, default: 0.5 },
  trim: { min: -20, max: 20, step: 1, default: 0 },
  snap: { min: 10, max: 50, step: 1, default: 35 },
  blip: { min: 0.03, max: 0.16, step: 0.01, default: 0.08 },
  align: { min: 0.015, max: 0.08, step: 0.005, default: 0.04 },
  low: { min: 36, max: 72, step: 1, default: 48 },
  high: { min: 64, max: 96, step: 1, default: 76 },
} as const;

export type MelodyParams = {
  fold: boolean;
  correct: boolean;
  window: number;
  strength: number;
  tune: boolean;
  trim: number;
  snap: boolean;
  snapCents: number;
  blip: number;
  align: boolean;
  alignSec: number;
  /** Drop pitches outside a congregational tessitura before folding or drawing. */
  singingRange: boolean;
  low: number;
  high: number;
};

export const DEFAULT_MELODY: MelodyParams = {
  fold: true,
  correct: true,
  window: MELODY_LIMITS.window.default,
  strength: MELODY_LIMITS.strength.default,
  tune: true,
  trim: MELODY_LIMITS.trim.default,
  snap: true,
  snapCents: MELODY_LIMITS.snap.default,
  blip: MELODY_LIMITS.blip.default,
  align: true,
  alignSec: MELODY_LIMITS.align.default,
  singingRange: true,
  low: MELODY_LIMITS.low.default,
  high: MELODY_LIMITS.high.default,
};

/** A vocal onset this close to a beat counts as on the grid. */
const GRID_ONSET_SEC = 0.05;
/** A short blip is only flattened when it lands within this many semitones. */
const NEAR_SEMITONES = 2;

/** Pitch class in `[0, 12)`, with C at both ends of that octave. */
export function foldMidi(midi: number): number {
  return ((midi % 12) + 12) % 12;
}

/** Signed step on the pitch-class circle, in `(-6, 6]`. */
export function circularDelta(from: number, to: number): number {
  return ((to - from + 18) % 12) - 6;
}

function circularMedian(values: number[]): number {
  let best = values[0] ?? 0;
  let bestCost = Infinity;
  for (const candidate of values) {
    let cost = 0;
    for (const value of values) cost += Math.abs(circularDelta(candidate, value));
    if (cost < bestCost) {
      bestCost = cost;
      best = candidate;
    }
  }
  return best;
}

/** A flicker onto another singer, wider than a semitone, is pulled back. */
const CORRECT_GATE = 1.5;

function correctPoint(midi: number, neighbors: number[], strength: number): number {
  if (neighbors.length < 2 || strength <= 0) return midi;
  const delta = circularDelta(midi, circularMedian(neighbors));
  if (Math.abs(delta) < CORRECT_GATE) return midi;
  return foldMidi(midi + strength * delta);
}

/** C3–E5: men from C3, women up to E5. Outside this, Basic Pitch is usually a harmonic. */
export function inSingingRange(midi: number, low: number, high: number): boolean {
  const n = Math.round(midi);
  return n >= low && n <= high;
}
export function estimateTuningCents(pitch: { midi: number; confidence: number }[]): number {
  if (pitch.length < 20) return 0;
  const sorted = pitch
    .map((p) => ({
      cents: (p.midi - Math.round(p.midi)) * 100,
      w: Math.max(0.01, p.confidence),
    }))
    .sort((a, b) => a.cents - b.cents);
  const total = sorted.reduce((sum, d) => sum + d.w, 0);
  let acc = 0;
  let cents = 0;
  for (const d of sorted) {
    acc += d.w;
    if (acc >= total / 2) {
      cents = d.cents;
      break;
    }
  }
  if (Math.abs(cents) < 3) return 0;
  return Math.max(-40, Math.min(40, Math.round(cents)));
}

export function formatCents(cents: number): string {
  const n = Math.round(cents);
  if (n === 0) return "0¢";
  return `${n > 0 ? "+" : ""}${n}¢`;
}

function nearestTime(sorted: number[], t: number): number | null {
  if (sorted.length === 0) return null;
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid]! < t) lo = mid + 1;
    else hi = mid;
  }
  const candidates = [sorted[lo], sorted[lo - 1]].filter((v): v is number => v != null);
  let best = candidates[0] ?? null;
  let bestD = Infinity;
  for (const c of candidates) {
    const d = Math.abs(c - t);
    if (d < bestD) {
      best = c;
      bestD = d;
    }
  }
  return best;
}

function beatMatchedOnsets(onsets: number[], beats: number[], tol: number): number[] {
  if (onsets.length === 0 || beats.length === 0) return [];
  const grid = [...beats].sort((a, b) => a - b);
  return onsets
    .filter((t) => {
      const beat = nearestTime(grid, t);
      return beat != null && Math.abs(beat - t) <= tol;
    })
    .sort((a, b) => a - b);
}

function snapToScale(midi: number, offset: number, snapSemitones: number): number {
  const note = Math.round(midi - offset);
  const center = note + offset;
  return Math.abs(midi - center) <= snapSemitones ? center : midi;
}

function runDuration(times: number[], a: number, b: number): number {
  if (b > a) return times[b]! - times[a]!;
  const prev = times[a - 1];
  const next = times[a + 1];
  if (prev != null && next != null) return next - prev;
  return 0.03;
}

/** A few frames on a nearby note, between two longer stretches of one note, go back. */
function flattenBlips<T extends { t_seconds: number; midi: number }>(
  points: T[],
  offset: number,
  blipSec: number,
): T[] {
  if (points.length < 3) return points;
  const noteOf = (midi: number) => Math.round(midi - offset);
  const runs: { a: number; b: number; note: number }[] = [];
  for (let i = 0; i < points.length; i++) {
    const note = noteOf(points[i]!.midi);
    const prev = runs[runs.length - 1];
    if (prev && prev.note === note) prev.b = i;
    else runs.push({ a: i, b: i, note });
  }
  const times = points.map((p) => p.t_seconds);
  const out = points.map((p) => ({ ...p }));
  for (let r = 1; r < runs.length - 1; r++) {
    const run = runs[r]!;
    const before = runs[r - 1]!;
    const after = runs[r + 1]!;
    if (before.note !== after.note) continue;
    const jump = Math.abs(run.note - before.note);
    if (jump < 1 || jump > NEAR_SEMITONES) continue;
    if (runDuration(times, run.a, run.b) > blipSec) continue;
    if (runDuration(times, before.a, before.b) <= blipSec) continue;
    if (runDuration(times, after.a, after.b) <= blipSec) continue;
    const target = before.note + offset;
    for (let i = run.a; i <= run.b; i++) out[i]!.midi = target;
  }
  return out;
}

function alignBoundaries<T extends { t_seconds: number; midi: number }>(
  points: T[],
  onsets: number[],
  alignSec: number,
  offset: number,
): T[] {
  if (onsets.length === 0 || alignSec <= 0) return points;
  const out = points.map((p) => ({ ...p }));
  let prevNote: number | null = null;
  for (let i = 0; i < out.length; i++) {
    const note = Math.round(out[i]!.midi - offset);
    if (prevNote !== null && note === prevNote) continue;
    const onset = nearestTime(onsets, out[i]!.t_seconds);
    prevNote = note;
    if (onset == null || Math.abs(onset - out[i]!.t_seconds) > alignSec) continue;
    const prevT = i > 0 ? out[i - 1]!.t_seconds + 0.004 : -Infinity;
    const nextT = i + 1 < out.length ? out[i + 1]!.t_seconds - 0.004 : Infinity;
    out[i]!.t_seconds = Math.min(nextT, Math.max(prevT, onset));
  }
  return out;
}

export type MelodyGrid = { beats: number[]; vocalOnsets: number[] };

export type DisplayedMelody = {
  pitch: PitchPoint[];
  notes: NoteSpan[];
  /** Semitones the scale is shifted. Positive is sharp of A440. */
  offset: number;
  tuningCents: number;
};

/**
 * Tune the scale to the band, hold steady notes flat, and nudge a note
 * start onto a vocal onset when that onset is already on a beat.
 * `heard` keeps the pitch the detector reported.
 */
export function displayMelody(
  pitch: PitchPoint[],
  notes: NoteSpan[],
  params: MelodyParams,
  grid?: MelodyGrid,
): DisplayedMelody {
  const sourcePitch = params.singingRange
    ? pitch.filter((p) => inSingingRange(p.midi, params.low, params.high))
    : pitch;
  const sourceNotes = params.singingRange
    ? notes.filter((n) => inSingingRange(n.midi, params.low, params.high))
    : notes;
  const tuningCents = estimateTuningCents(sourcePitch);
  const offset = params.tune ? (tuningCents + params.trim) / 100 : 0;
  const snapSemitones = params.snapCents / 100;
  let nextPitch = sourcePitch.map((p) => ({
    ...p,
    heard: p.midi,
    midi: params.snap ? snapToScale(p.midi, offset, snapSemitones) : p.midi,
  }));
  let nextNotes = sourceNotes.map((n) => ({
    ...n,
    heard: n.midi,
    midi: params.snap ? Math.round(n.midi - offset) + offset : n.midi,
  }));
  if (params.snap) {
    nextPitch = flattenBlips(nextPitch, offset, params.blip);
    nextNotes = nextNotes.map((n, i) => {
      if (n.t_end - n.t_start > params.blip) return n;
      const note = Math.round(n.midi - offset);
      const prev = nextNotes
        .slice(0, i)
        .reverse()
        .find((q) => q.t_end - q.t_start > params.blip && q.t_end <= n.t_start + 0.05);
      const following = nextNotes
        .slice(i + 1)
        .find((q) => q.t_end - q.t_start > params.blip && q.t_start >= n.t_end - 0.05);
      if (!prev || !following) return n;
      const held = Math.round(prev.midi - offset);
      if (held !== Math.round(following.midi - offset)) return n;
      const jump = Math.abs(note - held);
      if (jump < 1 || jump > NEAR_SEMITONES) return n;
      return { ...n, midi: held + offset };
    });
  }
  if (params.align && grid) {
    const onsets = beatMatchedOnsets(grid.vocalOnsets, grid.beats, GRID_ONSET_SEC);
    nextPitch = alignBoundaries(nextPitch, onsets, params.alignSec, offset);
    nextNotes = nextNotes.map((n) => {
      const onset = nearestTime(onsets, n.t_start);
      if (onset == null || Math.abs(onset - n.t_start) > params.alignSec) return n;
      const tStart = Math.min(n.t_end - 0.02, Math.max(0, onset));
      return tStart < n.t_end ? { ...n, t_start: tStart } : n;
    });
  }
  if (params.fold) {
    nextPitch = nextPitch.map((p) => ({ ...p, midi: foldMidi(p.midi - offset) }));
    nextNotes = nextNotes.map((n) => ({ ...n, midi: foldMidi(n.midi - offset) }));
    if (params.correct && params.strength > 0) {
      const span = params.window;
      const foldedPitch = nextPitch;
      const foldedNotes = nextNotes;
      nextPitch = foldedPitch.map((p, i) => ({
        ...p,
        midi: correctPoint(
          p.midi,
          foldedPitch
            .filter((q, j) => j !== i && Math.abs(q.t_seconds - p.t_seconds) <= span)
            .map((q) => q.midi),
          params.strength,
        ),
      }));
      nextNotes = foldedNotes.map((n, i) => {
        const mid = (n.t_start + n.t_end) / 2;
        return {
          ...n,
          midi: correctPoint(
            n.midi,
            foldedNotes
              .filter((q, j) => {
                if (j === i) return false;
                const other = (q.t_start + q.t_end) / 2;
                return Math.abs(other - mid) <= span;
              })
              .map((q) => q.midi),
            params.strength,
          ),
        };
      });
    }
    nextPitch = nextPitch.map((p) => ({ ...p, midi: p.midi + offset }));
    nextNotes = nextNotes.map((n) => ({ ...n, midi: n.midi + offset }));
  }
  return { pitch: nextPitch, notes: nextNotes, offset, tuningCents };
}

export function pitchLabel(midi: number, heard?: number): string {
  if (heard == null || Math.abs(heard - midi) < 0.05) return noteName(heard ?? midi);
  const pc = NOTE_NAMES[Math.round(foldMidi(midi)) % 12] ?? "C";
  return `${pc}  heard ${noteName(heard)}`;
}

const KIND_LABEL: Record<string, string> = {
  quarter: "1/4",
  eighth: "1/8",
  sixteenth: "1/16",
  triplet: "tri",
};

const SUBDIVISION: Record<string, number[]> = {
  sixteenth: [0.25, 0.5, 0.75],
  triplet: [1 / 3, 2 / 3],
};

type RhythmEvent = {
  band: string;
  t: number;
  phase: number;
  kind: string;
  error_ms: number;
  bar_index: number;
  show?: boolean;
};

type RhythmBar = {
  band: string;
  bar_index: number;
  t_start: number;
  t_end: number;
  pattern: string;
  stable?: boolean;
};

type LayerData = {
  pulse: { t_seconds: number; bpm: number; lock?: number }[];
  beats: number[];
  downbeats: number[];
  chords: { name: string; t_start: number; t_end: number }[];
  pitch: PitchPoint[];
  notes: NoteSpan[];
  onsets: { band: string; hz_lo: number; hz_hi: number; times?: number[] }[];
  rhythm?: RhythmEvent[];
  rhythmBars?: RhythmBar[];
};

export function noteName(midi: number): string {
  const n = Math.round(midi);
  const oct = Math.floor(n / 12) - 1;
  return `${NOTE_NAMES[((n % 12) + 12) % 12]}${oct}`;
}

export function formatClock(t: number): string {
  const sign = t < 0 ? "-" : "";
  const abs = Math.abs(t);
  const m = Math.floor(abs / 60);
  const s = abs - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function pulseAt(
  pulse: { t_seconds: number; bpm: number }[],
  t: number,
): number | null {
  let bpm: number | null = null;
  for (const p of pulse) {
    if (p.t_seconds <= t) bpm = p.bpm;
    else break;
  }
  return bpm;
}

export function chordAt(
  chords: { name: string; t_start: number; t_end: number }[],
  t: number,
): string | null {
  for (const c of chords) {
    if (t >= c.t_start && t < c.t_end) return c.name;
  }
  return null;
}

/** What the expanded row is showing at one instant. Empty when nothing is near. */
export function describeAt(
  kind: "pulse" | "beats" | "chords" | "pitch" | "onset",
  t: number,
  data: LayerData,
  band?: string,
): string[] {
  if (kind === "chords") {
    const c = data.chords.find((span) => t >= span.t_start && t < span.t_end);
    if (!c) return [];
    return [`${c.name}`, `${formatClock(c.t_start)} – ${formatClock(c.t_end)}`];
  }
  if (kind === "pulse") {
    const p = nearest(
      data.pulse.filter((pt) => Math.abs(pt.t_seconds - t) < 1),
      t,
      (pt) => pt.t_seconds,
    );
    if (!p) return [];
    const lock = p.lock == null ? "" : `  lock ${p.lock.toFixed(2)}`;
    return [`${p.bpm.toFixed(1)} BPM${lock}`, formatClock(p.t_seconds)];
  }
  if (kind === "beats") {
    const beat = nearest(
      data.beats.filter((bt) => Math.abs(bt - t) < 0.25),
      t,
      (bt) => bt,
    );
    if (beat == null) return [];
    const prev = [...data.beats].reverse().find((bt) => bt < beat - 1e-4);
    const down = data.downbeats.some((d) => Math.abs(d - beat) < 0.03);
    const gap = prev == null ? "" : `  ${(beat - prev) * 1000 | 0} ms from previous`;
    return [`${down ? "downbeat" : "beat"}  ${formatClock(beat)}${gap}`];
  }
  if (kind === "pitch") {
    const lines: string[] = [];
    for (const n of data.notes) {
      if (t >= n.t_start && t < n.t_end) {
        lines.push(
          `${pitchLabel(n.midi, n.heard)}  ${n.confidence.toFixed(2)}  ${formatClock(n.t_start)}–${formatClock(n.t_end)}`,
        );
      }
    }
    const p = nearest(
      data.pitch.filter((pt) => Math.abs(pt.t_seconds - t) < 0.12),
      t,
      (pt) => pt.t_seconds,
    );
    if (p) lines.push(`contour ${pitchLabel(p.midi, p.heard)}  ${p.confidence.toFixed(2)}`);
    return lines;
  }
  const row = data.onsets.find((o) => o.band === band);
  if (!row) return [];
  const times = row.times ?? [];
  const hit = nearest(
    times.filter((ot) => Math.abs(ot - t) < 0.12),
    t,
    (ot) => ot,
  );
  const lines: string[] = [];
  if (hit != null) {
    const prev = [...times].reverse().find((ot) => ot < hit - 1e-4);
    const gap = prev == null ? "" : `  ${(hit - prev) * 1000 | 0} ms from previous`;
    lines.push(
      `${row.band}  ${Math.round(row.hz_lo)}–${Math.round(row.hz_hi)} Hz`,
      `${formatClock(hit)}${gap}`,
    );
  }
  const event = nearest(
    (data.rhythm ?? []).filter((e) => e.band === band && Math.abs(e.t - t) < 0.12),
    t,
    (e) => e.t,
  );
  if (event) {
    lines.push(
      `${event.kind}  phase ${event.phase.toFixed(2)}  ${Math.round(event.error_ms)} ms`,
    );
  }
  const bar = (data.rhythmBars ?? []).find(
    (b) => b.band === band && t >= b.t_start && t < b.t_end,
  );
  if (bar) lines.push(bar.stable ? `${bar.pattern}  steady` : bar.pattern);
  return lines;
}

function nearest<T>(items: T[], t: number, timeOf: (item: T) => number): T | null {
  let best: T | null = null;
  let bestD = Infinity;
  for (const item of items) {
    const d = Math.abs(timeOf(item) - t);
    if (d < bestD) {
      best = item;
      bestD = d;
    }
  }
  return best;
}

function chordColor(name: string): string {
  const minor = name.endsWith("m");
  const root = minor ? name.slice(0, -1) : name;
  const roots = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const idx = roots.indexOf(root);
  if (idx < 0) return "rgba(160,160,170,0.45)";
  return `hsla(${idx * 30}, ${minor ? 40 : 65}%, ${minor ? 42 : 55}%, 0.85)`;
}

function PitchControls({
  params,
  onChange,
  melody,
  onMelody,
  tuningCents,
}: {
  params: PitchParams;
  onChange: (next: PitchParams) => void;
  melody: MelodyParams;
  onMelody: (next: MelodyParams) => void;
  tuningCents: number;
}) {
  const set = (key: keyof PitchParams) => (value: number) =>
    onChange({ ...params, [key]: value });
  const tuned = melody.tune ? tuningCents + melody.trim : 0;
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.fold}
            onChange={(e) => onMelody({ ...melody, fold: e.target.checked })}
          />
          fold octaves
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.correct}
            disabled={!melody.fold}
            onChange={(e) => onMelody({ ...melody, correct: e.target.checked })}
          />
          correct jumps
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.tune}
            onChange={(e) => onMelody({ ...melody, tune: e.target.checked })}
          />
          tune {melody.tune ? formatCents(tuned) : ""}
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.snap}
            onChange={(e) => onMelody({ ...melody, snap: e.target.checked })}
          />
          snap to notes
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.align}
            onChange={(e) => onMelody({ ...melody, align: e.target.checked })}
          />
          align onsets
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={melody.singingRange}
            onChange={(e) => onMelody({ ...melody, singingRange: e.target.checked })}
          />
          singing range{" "}
          {melody.singingRange ? `${noteName(melody.low)}–${noteName(melody.high)}` : ""}
        </label>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {melody.fold && melody.correct && (
          <>
            <PitchSlider
              label="window"
              min={MELODY_LIMITS.window.min}
              max={MELODY_LIMITS.window.max}
              step={MELODY_LIMITS.window.step}
              value={melody.window}
              format={(v) => `${Math.round(v * 1000)} ms`}
              onChange={(window) => onMelody({ ...melody, window })}
            />
            <PitchSlider
              label="strength"
              min={MELODY_LIMITS.strength.min}
              max={MELODY_LIMITS.strength.max}
              step={MELODY_LIMITS.strength.step}
              value={melody.strength}
              format={(v) => v.toFixed(2)}
              onChange={(strength) => onMelody({ ...melody, strength })}
            />
          </>
        )}
        {melody.tune && (
          <PitchSlider
            label="trim"
            min={MELODY_LIMITS.trim.min}
            max={MELODY_LIMITS.trim.max}
            step={MELODY_LIMITS.trim.step}
            value={melody.trim}
            format={(v) => formatCents(v)}
            onChange={(trim) => onMelody({ ...melody, trim })}
          />
        )}
        {melody.snap && (
          <>
            <PitchSlider
              label="snap"
              min={MELODY_LIMITS.snap.min}
              max={MELODY_LIMITS.snap.max}
              step={MELODY_LIMITS.snap.step}
              value={melody.snapCents}
              format={(v) => `${v}¢`}
              onChange={(snapCents) => onMelody({ ...melody, snapCents })}
            />
            <PitchSlider
              label="blip"
              min={MELODY_LIMITS.blip.min}
              max={MELODY_LIMITS.blip.max}
              step={MELODY_LIMITS.blip.step}
              value={melody.blip}
              format={(v) => `${Math.round(v * 1000)} ms`}
              onChange={(blip) => onMelody({ ...melody, blip })}
            />
          </>
        )}
        {melody.align && (
          <PitchSlider
            label="align"
            min={MELODY_LIMITS.align.min}
            max={MELODY_LIMITS.align.max}
            step={MELODY_LIMITS.align.step}
            value={melody.alignSec}
            format={(v) => `${Math.round(v * 1000)} ms`}
            onChange={(alignSec) => onMelody({ ...melody, alignSec })}
          />
        )}
        {melody.singingRange && (
          <>
            <PitchSlider
              label="low"
              min={MELODY_LIMITS.low.min}
              max={MELODY_LIMITS.low.max}
              step={MELODY_LIMITS.low.step}
              value={melody.low}
              format={(v) => noteName(v)}
              onChange={(low) => onMelody({ ...melody, low: Math.min(low, melody.high - 1) })}
            />
            <PitchSlider
              label="high"
              min={MELODY_LIMITS.high.min}
              max={MELODY_LIMITS.high.max}
              step={MELODY_LIMITS.high.step}
              value={melody.high}
              format={(v) => noteName(v)}
              onChange={(high) => onMelody({ ...melody, high: Math.max(high, melody.low + 1) })}
            />
          </>
        )}
        <PitchSlider
          label="contour"
          min={PITCH_LIMITS.contour.min}
          max={PITCH_LIMITS.contour.max}
          step={PITCH_LIMITS.contour.step}
          value={params.contour}
          format={(v) => v.toFixed(2)}
          onChange={set("contour")}
        />
        <PitchSlider
          label="note"
          min={PITCH_LIMITS.note.min}
          max={PITCH_LIMITS.note.max}
          step={PITCH_LIMITS.note.step}
          value={params.note}
          format={(v) => v.toFixed(2)}
          onChange={set("note")}
        />
        <PitchSlider
          label="min note"
          min={PITCH_LIMITS.minNote.min}
          max={PITCH_LIMITS.minNote.max}
          step={PITCH_LIMITS.minNote.step}
          value={params.minNote}
          format={(v) => `${Math.round(v * 1000)} ms`}
          onChange={set("minNote")}
        />
        <PitchSlider
          label="max jump"
          min={PITCH_LIMITS.maxJump.min}
          max={PITCH_LIMITS.maxJump.max}
          step={PITCH_LIMITS.maxJump.step}
          value={params.maxJump}
          format={(v) => `${v} st`}
          onChange={set("maxJump")}
        />
        <PitchSlider
          label="max gap"
          min={PITCH_LIMITS.maxGap.min}
          max={PITCH_LIMITS.maxGap.max}
          step={PITCH_LIMITS.maxGap.step}
          value={params.maxGap}
          format={(v) => `${Math.round(v * 1000)} ms`}
          onChange={set("maxGap")}
        />
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Fold octaves draws every note from C to C. Singing range keeps only C3 to E5,
        the span a congregation actually sings, so an unfolded line is not four
        octaves of harmonics. Tune shifts that scale off A440
        by the band's median pitch; trim nudges it by hand. Snap holds a steady note
        flat, and a blip shorter than the blip time drops back onto the note around
        it when it is only a step or two away. Align moves a note's start onto a vocal
        onset when that onset is already on a beat and the start is only a few dozen
        milliseconds off. The rest of the note stays where it was sung.
        Stored down to contour 0.05 and notes 0.15, about every 23 ms. The sliders hide
        the rest while the recording plays. Each contour point is the loudest pitch bin,
        so a quieter harmonic under the noise stays out of the line until the recording
        is analysed again.
      </p>
    </div>
  );
}

function PitchSlider({
  label,
  min,
  max,
  step,
  value,
  format,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex min-w-[7.5rem] flex-1 flex-col gap-0.5 text-[10px] text-muted-foreground">
      <span className="flex justify-between gap-2">
        <span>{label}</span>
        <span className="tabular-nums text-foreground">{format(value)}</span>
      </span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function labelBackend(backend: string | undefined, status: "loading" | "ready" | "error"): string {
  if (!backend && status === "loading") return "…";
  if (backend === "beat_this") return "Beat This";
  if (backend === "basic_pitch") return "Basic Pitch";
  if (backend === "btc") return "BTC";
  if (backend === "template") return "template";
  if (!backend || backend === "missing" || backend === "model_missing") return "not installed";
  return backend;
}

function inView(t: number, view: ViewWindow): boolean {
  return t >= view.start - 0.05 && t <= view.end + 0.05;
}

/** Points inside the window, plus the neighbour on each side so a line
 *  continues to the edge instead of vanishing between samples. */
export function covering<T extends { t_seconds: number }>(points: T[], view: ViewWindow): T[] {
  if (points.length === 0) return points;
  let lo = 0;
  while (lo < points.length - 1) {
    const next = points[lo + 1];
    if (!next || next.t_seconds >= view.start) break;
    lo += 1;
  }
  let hi = lo;
  while (hi < points.length - 1) {
    const cur = points[hi];
    if (!cur || cur.t_seconds > view.end) break;
    hi += 1;
  }
  const first = points[lo];
  if (!first || first.t_seconds > view.end) return [];
  return points.slice(lo, hi + 1);
}

type RowGeom = {
  key: string;
  kind: "pulse" | "beats" | "chords" | "pitch" | "onset";
  y: number;
  h: number;
  color?: string;
};

function layoutRows(
  onsets: { band: string }[],
  expanded: ReadonlySet<string>,
): RowGeom[] {
  const rows: RowGeom[] = [
    { key: "pulse", kind: "pulse", y: 0, h: 0 },
    { key: "beats", kind: "beats", y: 0, h: 0 },
    { key: "chords", kind: "chords", y: 0, h: 0 },
    { key: "pitch", kind: "pitch", y: 0, h: 0 },
  ];
  if (onsets.length === 0) {
    rows.push({ key: "onsets", kind: "onset", y: 0, h: 0 });
  } else {
    for (const band of onsets) {
      rows.push({
        key: band.band,
        kind: "onset",
        y: 0,
        h: 0,
        color: BAND_COLOR[band.band],
      });
    }
  }
  let y = 0;
  for (const row of rows) {
    const open = expanded.has(row.key);
    const h =
      row.kind === "onset"
        ? open
          ? EXPANDED.onset
          : ROW.onset
        : open
          ? EXPANDED[row.kind]
          : ROW[row.kind];
    row.y = y;
    row.h = h;
    y += h;
  }
  return rows;
}

function drawRhythmGrid(
  ctx: CanvasRenderingContext2D,
  args: {
    y: number;
    h: number;
    view: ViewWindow;
    xOf: (t: number) => number;
    beats: number[];
    downbeats: number[];
    bars: RhythmBar[];
    events: RhythmEvent[];
    color: string;
  },
) {
  const { y, h, view, xOf, beats, downbeats, bars, events, color } = args;
  for (const t of beats) {
    if (!inView(t, view)) continue;
    const strong = downbeats.some((d) => Math.abs(d - t) < 0.03);
    ctx.strokeStyle = strong ? "rgba(226,232,240,0.5)" : "rgba(226,232,240,0.18)";
    ctx.lineWidth = strong ? 1.25 : 1;
    ctx.beginPath();
    ctx.moveTo(xOf(t), y + 1);
    ctx.lineTo(xOf(t), y + h - 1);
    ctx.stroke();
  }
  ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";
  for (const bar of bars) {
    const phases = bar.stable ? SUBDIVISION[bar.pattern] : undefined;
    if (phases) {
      const inside = beats.filter((t) => t >= bar.t_start - 1e-3 && t < bar.t_end - 1e-3);
      for (const t0 of inside) {
        const next = beats.find((t) => t > t0 + 1e-4);
        if (next == null || next > bar.t_end + 1e-3) continue;
        const span = next - t0;
        ctx.strokeStyle = "rgba(226,232,240,0.16)";
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        for (const phase of phases) {
          const t = t0 + phase * span;
          if (!inView(t, view)) continue;
          ctx.beginPath();
          ctx.moveTo(xOf(t), y + 1);
          ctx.lineTo(xOf(t), y + h - 1);
          ctx.stroke();
        }
        ctx.setLineDash([]);
      }
    }
    const x1 = xOf(bar.t_start);
    const x2 = xOf(bar.t_end);
    if (x2 - x1 > 40 && bar.t_end > view.start && bar.t_start < view.end) {
      ctx.fillStyle = "rgba(226,232,240,0.55)";
      ctx.textAlign = "left";
      ctx.fillText(bar.pattern, Math.max(2, x1) + 2, y + 22);
    }
  }
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  let lastX = -999;
  for (const event of events) {
    if (!inView(event.t, view)) continue;
    if (!KIND_LABEL[event.kind]) continue;
    const x = xOf(event.t);
    if (x - lastX < 8) continue;
    drawRhythmSymbol(ctx, x, y + h - 2, event.kind);
    lastX = x;
  }
}

/** A small notehead on the onset, stem up. Flags mark eighths and sixteenths. */
function drawRhythmSymbol(
  ctx: CanvasRenderingContext2D,
  x: number,
  baseline: number,
  kind: string,
) {
  ctx.save();
  ctx.translate(x, baseline);
  const headY = -2.1;
  ctx.beginPath();
  ctx.ellipse(0, headY, 2.5, 1.7, -0.45, 0, Math.PI * 2);
  ctx.fill();
  const stemX = 2;
  const stemTop = headY - 7.2;
  ctx.lineWidth = 0.9;
  ctx.beginPath();
  ctx.moveTo(stemX, headY - 0.3);
  ctx.lineTo(stemX, stemTop);
  ctx.stroke();
  const flags = kind === "sixteenth" ? 2 : kind === "eighth" || kind === "triplet" ? 1 : 0;
  for (let i = 0; i < flags; i++) {
    const y0 = stemTop + i * 2.3;
    ctx.beginPath();
    ctx.moveTo(stemX, y0);
    ctx.bezierCurveTo(stemX + 4.4, y0 + 1.1, stemX + 4, y0 + 3.2, stemX + 1.1, y0 + 4);
    ctx.stroke();
  }
  if (kind === "triplet") {
    ctx.font = "7px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillText("3", stemX + 2.4, stemTop + 3.2);
  }
  ctx.restore();
}

const READOUT_MS = 250;

/** Chord name and the side panel follow this. It never belongs to the timeline's render. */
export function useFollowedTime(timeRef: RefObject<number> | undefined): number {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!timeRef) return;
    const id = window.setInterval(() => bump((n) => n + 1), READOUT_MS);
    return () => window.clearInterval(id);
  }, [timeRef]);
  return timeRef?.current ?? 0;
}

const NowReadout = memo(function NowReadout({
  playhead,
  timeRef,
  pulse,
  chords,
}: {
  playhead: number;
  timeRef?: RefObject<number>;
  pulse: { t_seconds: number; bpm: number }[];
  chords: { name: string; t_start: number; t_end: number }[];
}) {
  const followed = useFollowedTime(timeRef);
  const at = timeRef ? followed : playhead;
  const bpmNow = pulseAt(pulse, at);
  const chordNow = chordAt(chords, at);
  return (
    <>
      <span>pulse {bpmNow === null ? "—" : `${bpmNow.toFixed(0)} BPM`}</span>
      <span>chord {chordNow ?? "—"}</span>
    </>
  );
});

/** The rule is moved from the playback loop. A React `left` would snap it backwards on every panel refresh. */
const PlayheadRule = memo(function PlayheadRule({
  ruleRef,
  left,
}: {
  ruleRef?: Ref<HTMLDivElement>;
  left?: string;
}) {
  return (
    <div
      ref={ruleRef}
      className="pointer-events-none absolute inset-y-0 z-20 w-px bg-foreground"
      style={left != null ? { left } : undefined}
    />
  );
});

export function MusicLayers({
  data,
  view,
  playhead,
  status,
  error,
  onSeek,
  soloBand,
  onSoloBand,
  pendingLabel,
  playheadRuleRef,
  playheadTimeRef,
}: {
  data: InspectMusicResponse | null;
  view: ViewWindow;
  playhead: number;
  status: "loading" | "ready" | "error";
  error?: string;
  onSeek?: (t: number) => void;
  soloBand?: string | null;
  onSoloBand?: (band: string | null) => void;
  pendingLabel?: string;
  /** When set, the inspector paints this rule from the audio clock instead of `playhead`. */
  playheadRuleRef?: Ref<HTMLDivElement>;
  /** Live playback clock. Read by the chord label so the plot itself does not re-render. */
  playheadTimeRef?: RefObject<number>;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const [pitchParams, setPitchParams] = useState<PitchParams>(DEFAULT_PITCH_PARAMS);
  const [melody, setMelody] = useState<MelodyParams>(DEFAULT_MELODY);
  const pulse = data?.pulse ?? [];
  const chords = data?.chords ?? [];
  const beats = data?.beats ?? [];
  const downbeats = data?.downbeats ?? [];
  const notes = data?.notes ?? [];
  const pitch = data?.pitch ?? [];
  const onsets = data?.onsets ?? [];
  const rhythm = data?.rhythm ?? [];
  const rhythmBars = data?.rhythm_bars ?? [];
  const vocalOnsets = onsets.find((row) => row.band === "vocals")?.times;
  const shownPitch = useMemo(() => {
    const filtered = visiblePitch(pitch, notes, pitchParams);
    return displayMelody(filtered.pitch, filtered.notes, melody, {
      beats,
      vocalOnsets: vocalOnsets ?? [],
    });
  }, [pitch, notes, pitchParams, melody, beats, vocalOnsets]);
  const rows = layoutRows(onsets, expanded);
  const height = rows.reduce((sum, row) => sum + row.h, 0);
  const layerData: LayerData = {
    pulse,
    beats,
    downbeats,
    chords,
    pitch: shownPitch.pitch,
    notes: shownPitch.notes,
    onsets,
    rhythm,
    rhythmBars,
  };
  const through = data?.through_seconds ?? data?.duration_seconds ?? 0;
  const duration = data?.duration_seconds ?? 0;
  const filling = status === "loading" && data != null && through < duration - 0.05;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || view.span <= 0) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const cssW = parent.clientWidth;
      if (cssW === 0) return;
      if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(height * dpr)) {
        canvas.width = Math.floor(cssW * dpr);
        canvas.height = Math.floor(height * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, height);
      ctx.fillStyle = "rgba(255,255,255,0.03)";
      ctx.fillRect(0, 0, cssW, height);

      const xOf = (t: number) => ((t - view.start) / view.span) * cssW;
      const open = (key: string) => expanded.has(key);

      for (const row of rows) {
        const y = row.y;
        if (row.kind === "pulse") {
          const visiblePulse = covering(pulse, view);
          if (visiblePulse.length > 0) {
            const bpms = visiblePulse.map((p) => p.bpm);
            const dataLo = Math.min(...bpms);
            const dataHi = Math.max(...bpms);
            const mid = (dataLo + dataHi) / 2;
            const half = Math.max(15, (dataHi - dataLo) / 2);
            const axisLo = mid - half;
            const axisHi = mid + half;
            const yOf = (bpm: number) =>
              y + row.h - ((bpm - axisLo) / (axisHi - axisLo)) * (row.h - 8) - 4;
            ctx.strokeStyle = "rgba(226,232,240,0.9)";
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            visiblePulse.forEach((p, i) => {
              const x = xOf(p.t_seconds);
              const py = yOf(p.bpm);
              const prev = visiblePulse[i - 1];
              if (i === 0 || !prev || p.t_seconds - prev.t_seconds > 1.5) ctx.moveTo(x, py);
              else ctx.lineTo(x, py);
            });
            ctx.stroke();
            ctx.fillStyle = "rgba(226,232,240,0.55)";
            ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
            ctx.textAlign = "right";
            ctx.fillText(`${Math.round(dataHi)}`, cssW - 4, yOf(dataHi));
            if (dataHi - dataLo > 1) ctx.fillText(`${Math.round(dataLo)}`, cssW - 4, yOf(dataLo));
            if (open("pulse")) {
              ctx.textAlign = "center";
              let lastX = -999;
              for (const p of visiblePulse) {
                const x = xOf(p.t_seconds);
                if (x - lastX < 36) continue;
                ctx.fillText(`${Math.round(p.bpm)}`, x, yOf(p.bpm) - 4);
                lastX = x;
              }
            }
            ctx.textAlign = "left";
          }
        } else if (row.kind === "beats") {
          let prevT: number | null = null;
          for (const t of beats) {
            const shown = inView(t, view);
            if (shown) {
              const x = xOf(t);
              const strong = downbeats.some((d) => Math.abs(d - t) < 0.03);
              ctx.strokeStyle = strong ? "rgba(52,211,153,0.95)" : "rgba(52,211,153,0.45)";
              ctx.lineWidth = strong ? 1.5 : 1;
              ctx.beginPath();
              ctx.moveTo(x, y + 2);
              ctx.lineTo(x, y + row.h - 2);
              ctx.stroke();
            }
            if (open("beats") && prevT != null && shown) {
              const x0 = xOf(prevT);
              const x1 = xOf(t);
              if (x1 - x0 > 28) {
                ctx.fillStyle = "rgba(226,232,240,0.7)";
                ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
                ctx.textAlign = "center";
                ctx.fillText(`${Math.round((t - prevT) * 1000)}`, (x0 + x1) / 2, y + 11);
              }
            }
            prevT = t;
          }
        } else if (row.kind === "chords") {
          ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
          ctx.textBaseline = "middle";
          ctx.textAlign = "left";
          for (const c of chords) {
            if (c.t_end < view.start || c.t_start > view.end) continue;
            const x1 = Math.max(0, xOf(c.t_start));
            const x2 = Math.min(cssW, xOf(c.t_end));
            if (x2 - x1 < 1) continue;
            ctx.fillStyle = chordColor(c.name);
            ctx.fillRect(x1, y + 3, x2 - x1, row.h - 6);
            if (!open("chords") && x2 - x1 > 22) {
              ctx.fillStyle = "rgba(0,0,0,0.85)";
              ctx.fillText(c.name, x1 + 3, y + row.h / 2);
            }
          }
        } else if (row.kind === "pitch") {
          const visibleNotes = shownPitch.notes.filter(
            (n) => n.t_end >= view.start && n.t_start <= view.end,
          );
          const pitches = covering(shownPitch.pitch, view);
          const folded = melody.fold;
          const off = shownPitch.offset;
          const samples = [...visibleNotes.map((n) => n.midi), ...pitches.map((p) => p.midi)];
          const dataLo = folded ? off : samples.length ? Math.min(...samples) : 48 + off;
          const dataHi = folded ? 12 + off : samples.length ? Math.max(...samples) : 72 + off;
          const midiSpan = Math.max(12, dataHi - dataLo);
          const axisHi = dataHi;
          const yMidi = (midi: number) => y + 4 + ((axisHi - midi) / midiSpan) * (row.h - 8);
          if (open("pitch")) {
            ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
            ctx.textAlign = "right";
            ctx.textBaseline = "middle";
            const first = folded ? 0 : Math.floor(axisHi - midiSpan - off);
            const last = folded ? 12 : Math.ceil(axisHi - off);
            for (let pc = first; pc <= last; pc++) {
              const at = pc + off;
              const py = yMidi(at);
              const isC = ((pc % 12) + 12) % 12 === 0;
              ctx.strokeStyle = isC ? "rgba(226,232,240,0.34)" : "rgba(226,232,240,0.16)";
              ctx.lineWidth = 1;
              ctx.beginPath();
              ctx.moveTo(0, py);
              ctx.lineTo(cssW, py);
              ctx.stroke();
              if (isC) {
                ctx.fillStyle = "rgba(226,232,240,0.55)";
                ctx.fillText(folded ? "C" : noteName(pc), cssW - 4, py);
              }
            }
            ctx.textAlign = "left";
          }
          for (const n of visibleNotes) {
            const x1 = Math.max(0, xOf(n.t_start));
            const x2 = Math.min(cssW, xOf(n.t_end));
            ctx.fillStyle = `rgba(167,139,250,${0.25 + 0.6 * n.confidence})`;
            ctx.fillRect(x1, yMidi(n.midi) - 2, Math.max(1, x2 - x1), 4);
          }
          if (pitches.length > 1) {
            ctx.strokeStyle = "rgba(196,181,253,0.95)";
            ctx.lineWidth = 1.25;
            ctx.beginPath();
            pitches.forEach((p, i) => {
              const x = xOf(p.t_seconds);
              const py = yMidi(p.midi);
              const prev = pitches[i - 1];
              const broken =
                i === 0 ||
                !prev ||
                p.t_seconds - prev.t_seconds > pitchParams.maxGap ||
                Math.abs(p.midi - prev.midi) > pitchParams.maxJump;
              if (broken) ctx.moveTo(x, py);
              else ctx.lineTo(x, py);
            });
            ctx.stroke();
          }
        } else {
          const band = onsets.find((b) => b.band === row.key);
          if (open(row.key)) {
            drawRhythmGrid(ctx, {
              y,
              h: row.h,
              view,
              xOf,
              beats,
              downbeats,
              bars: rhythmBars.filter((b) => b.band === row.key),
              events: rhythm.filter((e) => e.band === row.key && e.show),
              color: BAND_COLOR[row.key] ?? "rgba(226,232,240,0.85)",
            });
          }
          ctx.strokeStyle = BAND_COLOR[row.key] ?? "rgba(226,232,240,0.7)";
          ctx.lineWidth = 1;
          let prevT: number | null = null;
          for (const t of band?.times ?? []) {
            const shown = inView(t, view);
            if (shown) {
              const x = xOf(t);
              ctx.beginPath();
              ctx.moveTo(x, y + 2);
              ctx.lineTo(x, y + row.h - 2);
              ctx.stroke();
            }
            if (open(row.key) && prevT != null && shown) {
              const x0 = xOf(prevT);
              const x1 = xOf(t);
              if (x1 - x0 > 28) {
                ctx.fillStyle = "rgba(226,232,240,0.75)";
                ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
                ctx.textAlign = "center";
                ctx.fillText(`${Math.round((t - prevT) * 1000)}`, (x0 + x1) / 2, y + 11);
              }
            }
            prevT = t;
          }
        }
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(parent);
    return () => observer.disconnect();
    // `rows` is derived from `expanded`, `onsets` and `height`. Leaving it out
    // of the dependencies keeps playback, which only moves the playhead, from
    // repainting the canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    beats,
    chords,
    data,
    downbeats,
    expanded,
    height,
    onsets,
    pitchParams,
    pulse,
    rhythm,
    rhythmBars,
    shownPitch,
    melody.fold,
    view,
  ]);

  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setTip(null);
  };

  const showPlot = data != null;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <NowReadout
          playhead={playhead}
          timeRef={playheadTimeRef}
          pulse={data ? pulse : []}
          chords={data ? chords : []}
        />
        <span>beats {labelBackend(data?.beat_backend, status)}</span>
        <span>chords {labelBackend(data?.chord_backend, status)}</span>
        <span>pitch {labelBackend(data?.pitch_backend, status)}</span>
        <span>
          {data?.source === "stems"
            ? "beats on the mix · chords on other · pitch on vocals"
            : "onsets kick · bass · body · strum · air"}
        </span>
        {data?.stage === "separating" && <span>separating</span>}
        {filling && <span>through {formatClock(through)}</span>}
        {soloBand && <span>hearing {soloBand}</span>}
      </div>
      {expanded.has("pitch") && (
        <PitchControls
          params={pitchParams}
          onChange={setPitchParams}
          melody={melody}
          onMelody={setMelody}
          tuningCents={shownPitch.tuningCents}
        />
      )}
      {status === "loading" && !data && (
        <div className="flex h-24 items-center justify-center rounded-md border border-border text-xs text-muted-foreground">
          {pendingLabel ?? "Analysing the first 30 seconds…"}
        </div>
      )}
      {status === "error" && (
        <div className="rounded-md border border-destructive/40 px-3 py-2 text-xs text-destructive">
          {error || "Music analysis failed"}
        </div>
      )}
      {showPlot && data && (
        <div className="flex">
          <div className="flex w-14 shrink-0 flex-col text-[10px] text-muted-foreground">
            {rows.map((row) => (
              <div
                key={row.key}
                role="button"
                tabIndex={0}
                className="flex cursor-pointer flex-col justify-center px-1 hover:text-foreground"
                style={{ height: row.h, color: row.color }}
                onClick={() => toggle(row.key)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle(row.key);
                  }
                }}
              >
                <span>{row.key}</span>
              </div>
            ))}
          </div>
          <div
            className="relative min-w-0 flex-1 cursor-pointer rounded-md border border-border bg-muted/30"
            style={{ height }}
            onPointerDown={(e) => {
              if (!onSeek || view.span <= 0) return;
              if ((e.target as HTMLElement).closest("button")) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const border = e.currentTarget.clientLeft;
              const width = e.currentTarget.clientWidth || rect.width;
              const frac = Math.min(Math.max((e.clientX - rect.left - border) / width, 0), 1);
              onSeek(viewFractionToTime(frac, view));
            }}
            onPointerMove={(e) => {
              if (view.span <= 0) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const x = e.clientX - rect.left - e.currentTarget.clientLeft;
              const y = e.clientY - rect.top - e.currentTarget.clientTop;
              const width = e.currentTarget.clientWidth || rect.width;
              const t = viewFractionToTime(Math.min(Math.max(x / width, 0), 1), view);
              const row = rows.find((r) => y >= r.y && y < r.y + r.h);
              if (!row || !expanded.has(row.key)) {
                setTip(null);
                return;
              }
              const lines = describeAt(row.kind, t, layerData, row.kind === "onset" ? row.key : undefined);
              setTip(lines.length ? { x: x + 10, y: y + 14, lines } : null);
            }}
            onPointerLeave={() => setTip(null)}
          >
            <canvas ref={canvasRef} className="block h-full w-full" />
            {expanded.has("chords") &&
              chords.map((c) => {
                if (c.t_end < view.start || c.t_start > view.end || view.span <= 0) return null;
                const row = rows.find((r) => r.key === "chords");
                if (!row) return null;
                const x1 = Math.max(0, ((c.t_start - view.start) / view.span) * 100);
                const x2 = Math.min(100, ((c.t_end - view.start) / view.span) * 100);
                if (x2 - x1 <= 0) return null;
                return (
                  <div
                    key={`${c.name}-${c.t_start}`}
                    className="pointer-events-none absolute flex items-center overflow-visible whitespace-nowrap px-0.5 text-[10px] font-medium text-black"
                    style={{
                      left: `${x1}%`,
                      width: `${x2 - x1}%`,
                      top: row.y + 3,
                      height: row.h - 6,
                    }}
                  >
                    {c.name}
                  </div>
                );
              })}
            {rows
              .filter((row) => row.kind === "onset" && (expanded.has(row.key) || soloBand === row.key))
              .map((row) => (
                <button
                  key={`solo-${row.key}`}
                  type="button"
                  className="absolute right-1 z-30 rounded border border-border bg-background/80 px-1.5 py-0.5 text-[10px] text-foreground"
                  style={{ top: row.y + 4 }}
                  onClick={() => onSoloBand?.(soloBand === row.key ? null : row.key)}
                >
                  {soloBand === row.key ? "soloed" : "solo"}
                </button>
              ))}
            {filling && (
              <div
                className="pointer-events-none absolute bottom-0 top-0 bg-background/45"
                style={{
                  left: `${Math.min(100, Math.max(0, timeToViewFraction(through, view) * 100))}%`,
                  right: 0,
                }}
              />
            )}
            <PlayheadRule
              ruleRef={playheadRuleRef}
              left={
                playheadRuleRef ? undefined : `${timeToViewFraction(playhead, view) * 100}%`
              }
            />
            {tip && (
              <div
                className="pointer-events-none absolute z-30 rounded border border-border bg-popover px-1.5 py-1 text-[10px] leading-snug text-popover-foreground shadow-sm"
                style={{ left: tip.x, top: tip.y }}
              >
                {tip.lines.map((line) => (
                  <div key={line}>{line}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
