/**
 * "Fits next" for the leader's library picker: a few library songs that
 * would sit well after the song being played now — close key, similar tempo,
 * same time signature — each with a short reason.
 *
 * Scored against the key the band is PLAYING the current song in this
 * service (its chart's key moved by the service transpose), not its library
 * key. A song that would fit if moved a few semitones is offered moved:
 * "Play in E (+2)".
 *
 * Scoring is the desktop library's (`musicalFit`); only the wording here is
 * the leader's.
 */

import type { BandSong } from "@/lib/band/protocol";
import type { LibrarySong } from "@/lib/band/librarySearch";
import { displayKey } from "@/lib/chords/chart";
import { transposeKey } from "@/lib/chords/chord";
import { fifthsDistance, musicalFit, type ChartMeta } from "@/lib/smartSearch/musicalFit";
import { normalizeTime } from "@/lib/smartSearch/parseQuery";

export interface FitSuggestion {
  song: LibrarySong;
  /** "Same key, similar tempo". */
  reason: string;
  /** The key it would be played in: its own, or the moved one. */
  key: string | null;
  /** Semitones it's moved from its written key (0: as written). */
  semitones: number;
}

/** Below this a song doesn't fit well enough to suggest. Same key alone clears it. */
const MIN_SCORE = 0.4;
/** A move costs a little: the band has to think, the singer may strain. */
const MOVE_PENALTY = 0.1;
/** Further than this and it isn't the same song for the singer any more. */
const MAX_MOVE = 3;

/** What the band is playing now: the played key, the chart's tempo and time. */
export function playedMeta(song: BandSong): ChartMeta {
  return {
    key: displayKey(song.chart, song.transpose),
    tempo: song.chart?.tempo ?? null,
    time: song.chart?.time ?? null,
  };
}

function metaOf(s: LibrarySong): ChartMeta {
  return { key: s.key, tempo: s.tempo ?? null, time: s.time ?? null };
}

/** The smallest move (within MAX_MOVE) that puts `key` in `target`'s key. */
function moveInto(key: string, target: string): number | null {
  for (const s of [1, -1, 2, -2, 3, -3].filter((n) => Math.abs(n) <= MAX_MOVE)) {
    if (fifthsDistance(transposeKey(key, s), target) === 0) return s;
  }
  return null;
}

const isMinor = (key: string) => /^[A-G][#b]?(m(?!aj)|min|-)/.test(key.trim());

function reasonFor(now: ChartMeta, other: ChartMeta, semitones: number): string {
  const why: string[] = [];
  if (now.key && other.key) {
    const d = fifthsDistance(now.key, other.key);
    if (semitones !== 0) why.push(`play in ${other.key} (${semitones > 0 ? "+" : ""}${semitones})`);
    else if (d === 0) why.push(isMinor(now.key) === isMinor(other.key) ? "same key" : "relative key");
    else if (d === 1) why.push("close key");
  }
  if (now.tempo && other.tempo) {
    const gap = Math.abs(now.tempo - other.tempo);
    if (gap <= 5) why.push("same tempo");
    else if (gap <= 15) why.push("similar tempo");
  }
  const ta = normalizeTime(now.time);
  const tb = normalizeTime(other.time);
  if (ta && ta === tb) why.push(`also ${tb}`);
  const text = why.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Up to `limit` songs that fit after `current`, best first. Songs already in
 * the set are left out (`inSet` holds their song ids). Empty when the current
 * song's chart says nothing musical.
 */
export function fitsNext(
  current: BandSong | undefined,
  library: LibrarySong[],
  inSet: { has(songId: string): boolean },
  limit = 3,
): FitSuggestion[] {
  if (!current) return [];
  const now = playedMeta(current);
  if (!now.key && !now.tempo && !normalizeTime(now.time)) return [];

  const scored: (FitSuggestion & { score: number })[] = [];
  for (const song of library) {
    if (song.id === current.songId || inSet.has(song.id)) continue;
    const meta = metaOf(song);
    let best = { score: musicalFit(now, meta).score, meta, semitones: 0 };
    // A chart can be moved; worth it only when the written key is a stretch.
    if (song.hasChart && song.key && now.key && (fifthsDistance(now.key, song.key) ?? 0) > 1) {
      const s = moveInto(song.key, now.key);
      if (s !== null) {
        const moved = { ...meta, key: transposeKey(song.key, s) };
        const score = musicalFit(now, moved).score - MOVE_PENALTY;
        if (score > best.score) best = { score, meta: moved, semitones: s };
      }
    }
    if (best.score < MIN_SCORE) continue;
    const reason = reasonFor(now, best.meta, best.semitones);
    if (!reason) continue;
    scored.push({ song, reason, key: best.meta.key, semitones: best.semitones, score: best.score });
  }
  scored.sort(
    (a, b) =>
      b.score - a.score ||
      b.song.uses - a.song.uses ||
      a.song.title.localeCompare(b.song.title, undefined, { sensitivity: "base" }),
  );
  return scored.slice(0, limit).map(({ score: _score, ...rest }) => rest);
}
