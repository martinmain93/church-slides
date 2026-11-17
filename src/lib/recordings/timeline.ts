/**
 * Pure logic for the Golden Recording Editor timeline.
 *
 * Kept side-effect-free (mirrors `src/lib/presentation/state.ts`) so the
 * marker math and edit reducers are unit-testable without a DOM. The editor
 * component owns the React state and wiring; everything here is a pure
 * function over plain data.
 */

import type { GroundTruthEvent, SetlistItemSnapshot } from "@/types/generated";

/** A ground-truth transition with a stable client id for selection/drag. */
export interface TimelineMarker {
  id: string;
  t_seconds: number;
  item_idx: number;
  slide_idx: number;
}

let _seq = 0;

/** Monotonic client-only id. Markers are keyed by this for React + drag. */
export function newMarkerId(): string {
  _seq += 1;
  return `m${_seq}`;
}

export function toMarkers(events: GroundTruthEvent[]): TimelineMarker[] {
  return events.map((e) => ({ id: newMarkerId(), ...e }));
}

/** Strip client ids and return chronologically-sorted ground-truth events. */
export function toEvents(markers: TimelineMarker[]): GroundTruthEvent[] {
  return sortMarkers(markers).map(({ t_seconds, item_idx, slide_idx }) => ({
    t_seconds,
    item_idx,
    slide_idx,
  }));
}

// --- time <-> position conversions ------------------------------------------

export function clampTime(t: number, duration: number): number {
  if (!Number.isFinite(t)) return 0;
  if (duration <= 0) return 0;
  return Math.min(Math.max(t, 0), duration);
}

/** Time (seconds) -> fraction in [0, 1] of the timeline width. */
export function timeToFraction(t: number, duration: number): number {
  if (duration <= 0) return 0;
  return clampTime(t, duration) / duration;
}

/** Fraction in [0, 1] -> time (seconds). */
export function fractionToTime(fraction: number, duration: number): number {
  const f = Math.min(Math.max(fraction, 0), 1);
  return f * Math.max(duration, 0);
}

// --- zoom / viewport --------------------------------------------------------

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 64;
/** Each button press multiplies/divides the zoom by this. */
export const ZOOM_STEP = 2;

/** The slice of the recording currently drawn across the timeline's width. */
export interface ViewWindow {
  start: number;
  end: number;
  /** `end - start`, i.e. how many seconds one timeline width covers. */
  span: number;
}

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return MIN_ZOOM;
  return Math.min(Math.max(zoom, MIN_ZOOM), MAX_ZOOM);
}

/**
 * The visible window at `zoom`, centred on `center` where possible.
 *
 * Near either end the window butts against the boundary instead of centring,
 * so the timeline never shows empty space past the start or finish. At zoom 1
 * this is always the whole recording.
 */
export function viewWindow(
  duration: number,
  zoom: number,
  center: number,
): ViewWindow {
  const dur = Math.max(duration, 0);
  const span = dur / clampZoom(zoom);
  if (span <= 0) return { start: 0, end: 0, span: 0 };
  const half = span / 2;
  const start = Math.min(Math.max(center - half, 0), Math.max(dur - span, 0));
  return { start, end: start + span, span };
}

/** Time -> fraction [0,1] of the timeline width. Outside the window it goes
 *  <0 or >1, which callers use to cull off-screen markers. */
export function timeToViewFraction(t: number, view: ViewWindow): number {
  if (view.span <= 0) return 0;
  return (t - view.start) / view.span;
}

/** Fraction [0,1] of the timeline width -> time. */
export function viewFractionToTime(fraction: number, view: ViewWindow): number {
  const f = Math.min(Math.max(fraction, 0), 1);
  return view.start + f * view.span;
}

/** True when `t` is outside the window (with a small margin for the playhead
 *  so it doesn't re-centre on every frame at the edge). */
export function isOutsideView(t: number, view: ViewWindow, margin = 0): boolean {
  return t < view.start + margin || t > view.end - margin;
}

// --- marker reducers (return new arrays) ------------------------------------

export function sortMarkers(markers: TimelineMarker[]): TimelineMarker[] {
  return [...markers].sort((a, b) => a.t_seconds - b.t_seconds);
}

export function insertMarker(
  markers: TimelineMarker[],
  marker: TimelineMarker,
): TimelineMarker[] {
  return sortMarkers([...markers, marker]);
}

export function removeMarker(
  markers: TimelineMarker[],
  id: string,
): TimelineMarker[] {
  return markers.filter((m) => m.id !== id);
}

export function retimeMarker(
  markers: TimelineMarker[],
  id: string,
  t: number,
  duration: number,
): TimelineMarker[] {
  return sortMarkers(
    markers.map((m) =>
      m.id === id ? { ...m, t_seconds: clampTime(t, duration) } : m,
    ),
  );
}

export function retargetMarker(
  markers: TimelineMarker[],
  id: string,
  itemIdx: number,
  slideIdx: number,
): TimelineMarker[] {
  return markers.map((m) =>
    m.id === id ? { ...m, item_idx: itemIdx, slide_idx: slideIdx } : m,
  );
}

// --- dirty tracking ---------------------------------------------------------

/** True when the current markers differ from the originally-loaded events. */
export function isDirty(
  original: GroundTruthEvent[],
  current: TimelineMarker[],
): boolean {
  const a = [...original].sort((x, y) => x.t_seconds - y.t_seconds);
  const b = toEvents(current);
  if (a.length !== b.length) return true;
  for (let i = 0; i < a.length; i++) {
    const ai = a[i]!;
    const bi = b[i]!;
    if (
      Math.abs(ai.t_seconds - bi.t_seconds) > 1e-6 ||
      ai.item_idx !== bi.item_idx ||
      ai.slide_idx !== bi.slide_idx
    ) {
      return true;
    }
  }
  return false;
}

// --- playback state ----------------------------------------------------------

/**
 * The marker in effect at time `t` — the latest one at or before it.
 *
 * Returns null before the first transition, which is a real state rather
 * than an error: the audience is still looking at whatever preceded the
 * first cue (for a normal capture, the song's leading blank).
 *
 * Ties go to the last marker at that timestamp, matching what an operator
 * clicking twice on the same instant would end up projecting.
 */
export function activeMarkerAt(
  markers: TimelineMarker[],
  t: number,
): TimelineMarker | null {
  let best: TimelineMarker | null = null;
  for (const m of markers) {
    if (m.t_seconds <= t && (best === null || m.t_seconds >= best.t_seconds)) {
      best = m;
    }
  }
  return best;
}

/** The first marker strictly after `t`, or null when none remain. */
export function nextMarkerAt(
  markers: TimelineMarker[],
  t: number,
): TimelineMarker | null {
  let best: TimelineMarker | null = null;
  for (const m of markers) {
    if (m.t_seconds > t && (best === null || m.t_seconds < best.t_seconds)) {
      best = m;
    }
  }
  return best;
}

// --- labels -----------------------------------------------------------------

export interface SlideLabel {
  /** e.g. "Item 3 · Slide 2" (1-based for humans). */
  title: string;
  /** First few lyric tokens of the slide, or "" when unavailable. */
  preview: string;
}

/**
 * Human label for an (item, slide) pair from the recorded setlist snapshot.
 * The snapshot carries no song titles (only `song_id`), so a lyric-token
 * preview is the best available description.
 */
export function slideLabel(
  setlistContent: SetlistItemSnapshot[],
  itemIdx: number,
  slideIdx: number,
  previewTokens = 8,
): SlideLabel {
  const title = `Item ${itemIdx + 1} · Slide ${slideIdx + 1}`;
  const item = setlistContent.find((s) => s.item_idx === itemIdx);
  const tokens = item?.slides?.[slideIdx] ?? [];
  const preview = tokens.slice(0, previewTokens).join(" ");
  return { title, preview };
}

/** First few lyric tokens of a slide, joined; "" when blank/out-of-range. */
export function slidePreview(
  slides: string[][],
  slideIdx: number,
  previewTokens = 6,
): string {
  const tokens = slides[slideIdx] ?? [];
  return tokens.slice(0, previewTokens).join(" ");
}

/** Format seconds as M:SS.s for transport / marker tooltips. */
export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}
