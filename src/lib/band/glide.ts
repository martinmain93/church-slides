/**
 * A slow, eased horizontal scroll — gentler than the browser's own "smooth",
 * which is quick and can't be timed. Used to bring the current song into the
 * middle of the leader's song strip without a jolt.
 *
 * A touch, wheel or key on the element stops the glide where it is: the
 * person scrolling always wins. With reduced motion on, it jumps.
 */

export const GLIDE_MS = 800;

/** Slow in, slow out. */
const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

type Scroller = Pick<HTMLElement, "scrollLeft" | "addEventListener" | "removeEventListener">;

/** Glide `el` to `left`. Returns a function that stops it. */
export function glideScrollLeft(el: Scroller, left: number, ms = GLIDE_MS): () => void {
  const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const from = el.scrollLeft;
  if (reduced || Math.abs(left - from) < 1) {
    el.scrollLeft = left;
    return () => {};
  }
  let frame = 0;
  let start: number | null = null;
  const stop = () => {
    cancelAnimationFrame(frame);
    for (const ev of INTERRUPTS) el.removeEventListener(ev, stop);
  };
  const step = (now: number) => {
    start ??= now;
    const t = Math.min(1, (now - start) / ms);
    el.scrollLeft = from + (left - from) * ease(t);
    if (t < 1) frame = requestAnimationFrame(step);
    else stop();
  };
  for (const ev of INTERRUPTS) el.addEventListener(ev, stop, { passive: true });
  frame = requestAnimationFrame(step);
  return stop;
}

const INTERRUPTS = ["pointerdown", "touchstart", "wheel", "keydown"] as const;
