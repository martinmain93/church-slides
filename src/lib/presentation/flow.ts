/**
 * Continuous ("flow") display: the pure half.
 *
 * Slide mode shows one slide and switches. Continuous mode shows the whole
 * song as one column and keeps the sung words near an anchor line a little
 * above centre, so the congregation can read ahead — the overhead-projector
 * model. The sidecar sends a *track* (`FlowPositionMessage`) rather than a
 * position; this module evaluates it, mirroring `decision/flow.py::FlowTrack.at`
 * exactly, so the column moves smoothly between updates that arrive every
 * 1-3 s.
 *
 * Word indices count `tokenize` tokens slide by slide through the item's
 * resolved sequence — the same tokens `buildSetlistContent` ships to the
 * matcher — so an index from the sidecar lands on the same word here.
 */

import { tokenize } from "@/lib/parseSong";
import type { AIStatusEvent, FlowPositionMessage } from "@/types/generated";

export type DisplayMode = "slide" | "continuous";
/** `auto`: continuous while the AI is driving the screen, slides otherwise. */
export type DisplayPref = "auto" | DisplayMode;

export function resolveDisplayMode(
  pref: DisplayPref,
  aiMode: AIStatusEvent["mode"],
): DisplayMode {
  if (pref !== "auto") return pref;
  return aiMode === "active" ? "continuous" : "slide";
}

/** A received track, re-anchored on this machine's wall clock. */
export interface FlowTrack {
  itemIdx: number;
  word: number;
  rateWps: number;
  correction: number;
  settleSeconds: number;
  limit: number;
  jump: boolean;
  /** `Date.now()` at receipt — wall clock, so it survives the hop to the
   * audience window, which runs in another webview. */
  receivedAtMs: number;
}

export function trackFromMessage(msg: FlowPositionMessage, nowMs: number): FlowTrack {
  return {
    itemIdx: msg.item_idx,
    word: msg.word,
    rateWps: msg.rate_wps,
    correction: msg.correction ?? 0,
    settleSeconds: msg.settle_seconds ?? 2,
    limit: msg.limit,
    jump: msg.jump ?? false,
    receivedAtMs: nowMs,
  };
}

/** `FlowTrack.at` from `decision/flow.py`. */
export function trackWordAt(track: FlowTrack, nowMs: number): number {
  const dt = Math.max(0, (nowMs - track.receivedAtMs) / 1000);
  const settle = track.settleSeconds > 0 ? Math.min(1, dt / track.settleSeconds) : 1;
  return Math.min(
    track.limit,
    track.word + track.rateWps * dt + track.correction * settle,
  );
}

/** Global word index at which each slide starts, plus the total at the end. */
export function slideOffsets(slides: { words: string[] }[]): number[] {
  const out = [0];
  for (const s of slides) out.push(out[out.length - 1]! + s.words.length);
  return out;
}

/**
 * The slide a word falls on. A word at or past the end of a slide belongs to
 * the next slide that has words — the column stops at the gap *before* it,
 * which is where the congregation is waiting for it.
 */
export function slideAtWord(offsets: number[], word: number): number {
  const n = offsets.length - 1;
  for (let i = 0; i < n; i++) {
    if (offsets[i]! === offsets[i + 1]!) continue; // blank slide
    if (word < offsets[i + 1]!) return i;
  }
  // Past the last word: stay on the last slide with words.
  for (let i = n - 1; i >= 0; i--) {
    if (offsets[i]! < offsets[i + 1]!) return i;
  }
  return 0;
}

/** What the column is following: the AI's track, or a slide someone picked. */
export type FlowAnchor =
  | { kind: "track"; track: FlowTrack }
  | { kind: "slide"; itemIdx: number; slideIdx: number };

/**
 * The anchor to show for the item on screen. A track for another song is
 * transient — the position is about to follow it — so until it does, hold
 * the current slide rather than place foreign word indices in this column.
 */
export function anchorForItem(
  anchor: FlowAnchor,
  itemIdx: number,
  slideIdx: number,
): FlowAnchor {
  const same = anchor.kind === "track" ? anchor.track.itemIdx : anchor.itemIdx;
  return same === itemIdx ? anchor : { kind: "slide", itemIdx, slideIdx };
}

/** The word the anchor line should sit on at `nowMs`. */
export function anchorWord(
  anchor: FlowAnchor,
  offsets: number[],
  nowMs: number,
): number {
  if (anchor.kind === "track") return trackWordAt(anchor.track, nowMs);
  return offsets[Math.min(anchor.slideIdx, offsets.length - 1)] ?? 0;
}

/** One displayed line, with the token range it covers. */
export interface FlowLine {
  text: string;
  /** Global token index of the line's first word, and one past its last. */
  start: number;
  end: number;
}

export interface FlowSlideLayout {
  slideIdx: number;
  lines: FlowLine[];
}

/**
 * Split each slide into display lines and give every line its token range.
 *
 * Tokenizing line by line sums to tokenizing the slide, because `tokenize`
 * splits on whitespace and newlines are whitespace. Lines with no tokens
 * (punctuation only) get an empty range at the running index.
 */
export function layoutFlow(
  slides: { lyrics: string; words: string[] }[],
): FlowSlideLayout[] {
  const out: FlowSlideLayout[] = [];
  let g = 0;
  slides.forEach((slide, slideIdx) => {
    if (slide.words.length === 0) return;
    const lines: FlowLine[] = [];
    const slideStart = g;
    for (const text of slide.lyrics.split("\n")) {
      const n = tokenize(text).length;
      lines.push({ text, start: g, end: g + n });
      g += n;
    }
    // Defensive: if line tokenization ever disagrees with the slide's own
    // words, trust the words so every later slide keeps its true offset.
    g = slideStart + slide.words.length;
    out.push({ slideIdx, lines });
  });
  return out;
}
