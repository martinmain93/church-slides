/**
 * `cqw` container units, with a fallback for web views that predate them.
 *
 * The projected lyrics and scripture are sized in `cqw` so they scale to the
 * region they occupy rather than the window. Container units arrived in
 * Safari 16 / Chrome 105; the app's web view on an older Mac (macOS 10.15–12
 * without the latest Safari) drops a declaration it can't parse — so
 * `clamp(2rem, 5cqw, 5rem)` became no font-size at all, and the lyrics
 * rendered at 16px on the projector.
 *
 * Where `cqw` is supported this returns it unchanged. Where it isn't, it
 * measures the container with a ResizeObserver and returns the same length in
 * px — 1cqw is 1% of the container's content-box width, which is exactly
 * what `contentRect.width` reports — so both paths render identically.
 */

import { useLayoutEffect, useRef, useState } from "react";

const SUPPORTED =
  typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("width", "1cqw");

export interface ContainerUnits<T extends HTMLElement> {
  /** Attach to the element that has `container-type: inline-size`. */
  ref: React.RefObject<T | null>;
  /** `n` container-width percent, as a CSS length. For its *children*. */
  cqw(n: number): string;
}

export function useContainerUnits<T extends HTMLElement>(
  forceFallback = false,
): ContainerUnits<T> {
  const native = SUPPORTED && !forceFallback;
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (native) return;
    const el = ref.current;
    if (!el) return;
    const measure = (w: number) => setWidth((prev) => (prev === w ? prev : w));
    measure(el.getBoundingClientRect().width - horizontalPadding(el));
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) measure(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [native]);

  return {
    ref,
    cqw: (n: number) =>
      native
        ? `${n}cqw`
        : !width
          ? // Not measured yet (first paint), or mounted while hidden — a
            // 0px max-width would collapse the lyrics until the next resize.
            `${n}vw`
          : `${round((n * width) / 100)}px`,
  };
}

function horizontalPadding(el: HTMLElement): number {
  const style = getComputedStyle(el);
  return (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
