import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { glideScrollLeft } from "@/lib/band/glide";

/** A stand-in strip: just a scroll position and its listeners. */
function strip(scrollLeft = 0) {
  const listeners = new Map<string, () => void>();
  return {
    scrollLeft,
    addEventListener: (ev: string, fn: () => void) => listeners.set(ev, fn),
    removeEventListener: (ev: string) => listeners.delete(ev),
    fire: (ev: string) => listeners.get(ev)?.(),
    listeners,
  };
}

describe("glideScrollLeft", () => {
  let frames: ((t: number) => void)[] = [];
  const run = (t: number) => {
    const due = frames;
    frames = [];
    due.forEach((f) => f(t));
  };
  beforeEach(() => {
    frames = [];
    vi.stubGlobal("requestAnimationFrame", (f: (t: number) => void) => frames.push(f));
    vi.stubGlobal("cancelAnimationFrame", () => {
      frames = [];
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("eases slowly to the target over the glide time, then lets go", () => {
    const el = strip();
    glideScrollLeft(el as unknown as HTMLElement, 400, 800);
    run(0);
    expect(el.scrollLeft).toBe(0);
    run(200); // a quarter of the time: still near the start (eased in)
    expect(el.scrollLeft).toBeGreaterThan(0);
    expect(el.scrollLeft).toBeLessThan(100);
    run(400);
    expect(el.scrollLeft).toBeCloseTo(200);
    run(800);
    expect(el.scrollLeft).toBe(400);
    expect(frames).toHaveLength(0);
    expect(el.listeners.size).toBe(0);
  });

  it("stops where it is when the person touches the strip", () => {
    const el = strip();
    glideScrollLeft(el as unknown as HTMLElement, 400, 800);
    run(0);
    run(400);
    el.fire("pointerdown");
    const at = el.scrollLeft;
    run(800);
    expect(el.scrollLeft).toBe(at);
  });

  it("jumps when reduced motion is on", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce") }));
    const el = strip();
    glideScrollLeft(el as unknown as HTMLElement, 400);
    expect(el.scrollLeft).toBe(400);
    expect(frames).toHaveLength(0);
  });
});
