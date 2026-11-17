/**
 * Live chord and beat analysis, for watching.
 *
 * Diagnostic only — nothing in the decision path consumes it. Measured on the
 * approved goldens, adding this chord feature to the word matcher is worth
 * about +1% and sits right at the break-even point for detector accuracy
 * (docs/research/CHORD_DOWNBEAT.md). So the panel exists to answer, on live input, the
 * question the recordings cannot: what is it actually hearing?
 *
 * The sidecar sends only the audio *since the last tick*, about ten times a
 * second. This store owns the scrolling window, which is why it lives at module
 * scope like the other live stores — switching debug tabs must not throw the
 * history away.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";

/** Seconds of history the scope shows. */
export const WINDOW_S = 10;

/** Level samples per second, matching `music.analyser.LEVEL_HZ`. */
export const LEVEL_HZ = 50;

const MAX_LEVELS = WINDOW_S * LEVEL_HZ;

export interface ChordSegment {
  name: string;
  tStart: number;
  tEnd: number;
}

export interface ChordCandidate {
  name: string;
  confidence: number;
}

export interface MusicState {
  /** RMS envelope, oldest first, `LEVEL_HZ` samples per second. */
  levels: number[];
  /** Time of the newest level sample. */
  tEnd: number;
  /** Contiguous runs of one chord, for the overlay. */
  segments: ChordSegment[];
  beats: number[];
  candidates: ChordCandidate[];
  chord: string | null;
  bpm: number;
  chordsEnabled: boolean;
  beatsEnabled: boolean;
  realtimeFactor: number;
  /**
   * Largest recent gap between messages *arriving*, in seconds.
   *
   * Capture used to share a thread with Whisper, so a pass froze this
   * stream and then a burst drained the queue. ASR is on its own thread
   * now; this is still the right number if anything else stalls fan-out.
   * The scope uses it to size how far behind live it should render.
   */
  arrivalJitter: number;
  /** "btc" (the learned model) or "template" (the numpy fallback). */
  backend: string;
  /** Why the learned model is not in use, when it is not. */
  backendStatus: string;
  seen: boolean;
}

const EMPTY: MusicState = {
  levels: [],
  tEnd: 0,
  segments: [],
  beats: [],
  candidates: [],
  chord: null,
  bpm: 0,
  chordsEnabled: false,
  beatsEnabled: false,
  realtimeFactor: 0,
  arrivalJitter: 0,
  backend: "template",
  backendStatus: "model_missing",
  seen: false,
};

const store = createLiveStore<MusicState>(EMPTY);

/** Recent arrival gaps in seconds, newest last. */
let arrivals: number[] = [];
let lastArrival = 0;

function noteArrival(): number {
  const now = performance.now() / 1000;
  if (lastArrival > 0) {
    arrivals = [...arrivals, now - lastArrival].slice(-60);
  }
  lastArrival = now;
  if (arrivals.length < 5) return 0;
  // The worst of the recent past, not the mean: one stalled second is exactly
  // what the scope has to absorb, and an average hides it.
  return [...arrivals].sort((a, b) => a - b)[
    Math.floor(arrivals.length * 0.95)
  ] ?? 0;
}

pipeFromSidecar("music_analysis", store, (msg, prev) => {
  const arrivalJitter = noteArrival();
  const levels = [...prev.levels, ...msg.levels].slice(-MAX_LEVELS);
  const tEnd = msg.t_end;
  const cutoff = tEnd - WINDOW_S;

  // A chord run is extended rather than re-added, so the overlay draws one
  // labelled band per chord instead of one per tick.
  const tickStart = tEnd - msg.levels.length / LEVEL_HZ;
  let segments = prev.segments;
  if (msg.chord) {
    const last = segments[segments.length - 1];
    if (last && last.name === msg.chord) {
      segments = [...segments.slice(0, -1), { ...last, tEnd }];
    } else {
      segments = [...segments, { name: msg.chord, tStart: tickStart, tEnd }];
    }
  }
  segments = segments.filter((s) => s.tEnd > cutoff);

  return {
    levels,
    tEnd,
    segments,
    beats: [...prev.beats, ...(msg.beats ?? [])].filter((b) => b > cutoff),
    candidates: (msg.candidates ?? []).map((c) => ({
      name: c.name,
      confidence: c.confidence,
    })),
    chord: msg.chord ?? null,
    bpm: msg.bpm ?? 0,
    chordsEnabled: msg.chords_enabled ?? false,
    beatsEnabled: msg.beats_enabled ?? false,
    realtimeFactor: msg.realtime_factor ?? 0,
    arrivalJitter,
    backend: msg.chord_backend ?? "template",
    backendStatus: msg.backend_status ?? "model_missing",
    seen: true,
  };
});

export function useMusicAnalysis(): MusicState {
  return useLiveStore(store);
}

export function resetMusicAnalysis(): void {
  arrivals = [];
  lastArrival = 0;
  store.set(EMPTY);
}

/**
 * How far behind the newest audio the scope should draw, in seconds.
 *
 * Enough to cover the worst recent delivery gap, so a burst always lands
 * before the render point reaches it, plus a small margin. Clamped so a single
 * outlier cannot push the display a long way into the past.
 */
export function displayDelay(jitter: number): number {
  return Math.min(2.5, Math.max(0.35, jitter + 0.15));
}

/**
 * Turn chord and beat detection on or off, independently.
 *
 * They are separate switches because they cost and mean different things: the
 * chord stream is the one that could eventually inform the matcher, while the
 * beat grid is measured to be the weaker of the two and is mostly here to make
 * the chord stream legible.
 */
export async function setMusicAnalysis(
  chords: boolean,
  beats: boolean,
): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "set_music_analysis", chords, beats });
  // Optimistic; the sidecar confirms on its next broadcast.
  store.set((prev) => ({
    ...prev,
    chordsEnabled: chords,
    beatsEnabled: beats,
  }));
}
