/**
 * The scope's geometry, which is the part that can be confidently wrong.
 *
 * A chord band drawn at the wrong x still looks like a chord band, so a
 * screenshot would not catch it — and the whole point of this panel is to read
 * the analyser's *timing* off the picture. These tests pin the mapping from a
 * timestamp to a pixel, using a recording 2D context.
 */

import { describe, expect, it, vi } from "vitest";

import { draw } from "@/components/debug/MusicTab";
import {
  LEVEL_HZ,
  WINDOW_S,
  displayDelay,
  type MusicState,
} from "@/lib/musicAnalysis";

const W = 500;
const H = 100;

interface Rect {
  x: number;
  w: number;
}

function fakeCanvas() {
  const rects: Rect[] = [];
  const lines: number[] = [];
  const texts: { text: string; x: number }[] = [];
  const ctx = {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    fillRect: (x: number, _y: number, w: number) => rects.push({ x, w }),
    strokeRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: (x: number) => lines.push(x),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillText: (text: string, x: number) => texts.push({ text, x }),
    set fillStyle(_v: string) {},
    set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {},
    set font(_v: string) {},
    set textBaseline(_v: string) {},
  };
  const canvas = {
    clientWidth: W,
    clientHeight: H,
    width: 0,
    height: 0,
    getContext: () => ctx,
  } as unknown as HTMLCanvasElement;
  return { canvas, rects, lines, texts };
}

const BASE: MusicState = {
  levels: [],
  tEnd: 100,
  segments: [],
  beats: [],
  candidates: [],
  chord: null,
  bpm: 0,
  chordsEnabled: true,
  beatsEnabled: true,
  realtimeFactor: 0,
  arrivalJitter: 0,
  backend: "template",
  backendStatus: "model_missing",
  seen: true,
};

/** x for a timestamp, given the window ends at `tEnd`. */
const expectedX = (t: number, tEnd: number) =>
  ((t - (tEnd - WINDOW_S)) / WINDOW_S) * W;

describe("music scope geometry", () => {
  it("puts the newest audio at the right edge and the oldest at the left", () => {
    const { canvas, rects } = fakeCanvas();
    // A full window of level samples.
    const levels = Array(WINDOW_S * LEVEL_HZ).fill(0.5);
    draw(canvas, { ...BASE, levels });
    const xs = rects.map((r) => r.x);
    expect(Math.min(...xs)).toBeCloseTo(0, 0);
    expect(Math.max(...xs)).toBeGreaterThan(W - 5);
  });

  it("draws a chord band across exactly the seconds it covers", () => {
    const { canvas, rects, texts } = fakeCanvas();
    draw(canvas, {
      ...BASE,
      segments: [{ name: "Am", tStart: 94, tEnd: 96 }],
    });
    const band = rects.find((r) => Math.abs(r.x - expectedX(94, 100)) < 0.5);
    expect(band).toBeDefined();
    expect(band?.w).toBeCloseTo(expectedX(96, 100) - expectedX(94, 100), 1);
    expect(texts.some((t) => t.text === "Am")).toBe(true);
  });

  it("places a beat at its own timestamp", () => {
    const { canvas, lines } = fakeCanvas();
    draw(canvas, { ...BASE, beats: [95] });
    // Rounded to a half-pixel for a crisp 1px line.
    expect(lines).toContain(Math.round(expectedX(95, 100)) + 0.5);
  });

  it("clips history that has scrolled off the left edge", () => {
    const { canvas, rects } = fakeCanvas();
    draw(canvas, {
      ...BASE,
      segments: [{ name: "C", tStart: 80, tEnd: 95 }],
    });
    // Starts before the window: clamped to 0, never negative.
    const band = rects.find((r) => r.w > 100);
    expect(band?.x).toBeGreaterThanOrEqual(0);
  });

  it("draws the window ending where it is told, not at the newest audio", () => {
    // The scope renders behind live so a burst of messages lands before the
    // render point reaches it. Geometry must follow the view, not the data.
    const { canvas, lines } = fakeCanvas();
    draw(canvas, { ...BASE, tEnd: 100, beats: [95] }, 96);
    expect(lines).toContain(Math.round(expectedX(95, 96)) + 0.5);
  });

  it("survives a zero-sized canvas without drawing", () => {
    const { canvas, rects } = fakeCanvas();
    (canvas as unknown as { clientWidth: number }).clientWidth = 0;
    draw(canvas, { ...BASE, beats: [95] });
    expect(rects).toHaveLength(0);
  });
});

describe("display delay", () => {
  it("stays close to live when updates arrive smoothly", () => {
    expect(displayDelay(0)).toBeCloseTo(0.35, 2);
    expect(displayDelay(0.1)).toBeCloseTo(0.35, 2);
  });

  it("covers a bursty delivery gap", () => {
    // Whisper's pass blocks the capture thread for the better part of a
    // second; the scope has to sit at least that far back to stay smooth.
    expect(displayDelay(0.8)).toBeGreaterThan(0.8);
  });

  it("does not run away on a single outlier", () => {
    expect(displayDelay(30)).toBeLessThanOrEqual(2.5);
  });
});
