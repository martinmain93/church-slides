/**
 * How projected words look: the look's types, its options, and turning a
 * look into style. Pure, so every client draws slides the same way; the
 * desktop's storage and sync of the look are in `displayLook.ts`.
 */

import { createContext, useContext, type CSSProperties } from "react";

// Google Fonts, bundled through fontsource so they work offline. Only the
// weights `FONT_WEIGHTS` uses; a browser only fetches a file once text
// actually renders in it.
import "@fontsource/eb-garamond/400.css";
import "@fontsource/eb-garamond/500.css";
import "@fontsource/eb-garamond/600.css";
import "@fontsource/cormorant-garamond/500.css";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/cormorant-garamond/700.css";
import "@fontsource/libre-caslon-text/400.css";
import "@fontsource/libre-caslon-text/700.css";
import "@fontsource/radley/400.css";
import "@fontsource/young-serif/400.css";

export type DisplayTheme = "dark" | "light" | "overhead";
/** `auto` = the theme's own typeface (sans, or EB Garamond for the overhead). */
export type DisplayFont =
  | "auto"
  | "sans"
  | "rounded"
  | "eb-garamond"
  | "cormorant"
  | "caslon"
  | "radley"
  | "young-serif"
  | "times"
  | "georgia";
export type DisplaySize = "s" | "m" | "l" | "xl";
/** Relative to each face — `bold` is the weight the lyrics always had. */
export type DisplayWeight = "regular" | "semibold" | "bold";

export interface DisplayLook {
  theme: DisplayTheme;
  font: DisplayFont;
  size: DisplaySize;
  weight: DisplayWeight;
}

export const DEFAULT_LOOK: DisplayLook = {
  theme: "dark",
  font: "auto",
  size: "m",
  weight: "bold",
};

export const THEME_OPTIONS: { value: DisplayTheme; label: string; hint: string }[] = [
  { value: "dark", label: "White on black", hint: "The default. Best in a dark room." },
  { value: "light", label: "Black on white", hint: "For bright rooms and weak projectors." },
  { value: "overhead", label: "Overhead projector", hint: "Warm lit glass, like a transparency." },
];

/** Vetted for legibility at a distance. Sans, rounded, Times and Georgia
 * ship with macOS; the rest are bundled Google Fonts (imports above). */
export const FONT_OPTIONS: { value: DisplayFont; label: string; hint: string }[] = [
  { value: "auto", label: "Match the theme", hint: "Sans, or EB Garamond on the overhead" },
  { value: "sans", label: "Modern sans", hint: "Clean and neutral" },
  { value: "rounded", label: "Rounded", hint: "Soft and friendly" },
  { value: "eb-garamond", label: "EB Garamond", hint: "The hymnal classic" },
  { value: "cormorant", label: "Cormorant Garamond", hint: "Elegant, high contrast" },
  { value: "caslon", label: "Libre Caslon Text", hint: "Sturdy, bookish" },
  { value: "radley", label: "Radley", hint: "Soft, hand-cut feel" },
  { value: "young-serif", label: "Young Serif", hint: "Heavy and warm" },
  { value: "times", label: "Times New Roman", hint: "The 90s word-processor classic" },
  { value: "georgia", label: "Georgia", hint: "A warmer serif" },
];

export const SIZE_OPTIONS: { value: DisplaySize; label: string }[] = [
  { value: "s", label: "Small" },
  { value: "m", label: "Standard" },
  { value: "l", label: "Large" },
  { value: "xl", label: "Extra large" },
];

export const WEIGHT_OPTIONS: { value: DisplayWeight; label: string }[] = [
  { value: "regular", label: "Regular" },
  { value: "semibold", label: "Semibold" },
  { value: "bold", label: "Bold" },
];

/** Multiplier on each stage's own fitted size; `m` is exactly the old size. */
export const SIZE_SCALE: Record<DisplaySize, number> = { s: 0.85, m: 1, l: 1.15, xl: 1.3 };

const FONT_STACK: Record<Exclude<DisplayFont, "auto">, string> = {
  // `undefined` would do for sans (the Tailwind `font-sans` class), but an
  // explicit stack keeps the resolved style self-contained.
  sans: 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  // `ui-rounded` (SF Pro Rounded) first: it has every weight, where Arial
  // Rounded is bold-only and would ignore the weight setting.
  rounded: 'ui-rounded, "SF Pro Rounded", "Arial Rounded MT Bold", "Helvetica Neue", Arial, sans-serif',
  "eb-garamond": '"EB Garamond", Garamond, Georgia, serif',
  cormorant: '"Cormorant Garamond", "EB Garamond", Garamond, Georgia, serif',
  caslon: '"Libre Caslon Text", "Big Caslon", Georgia, serif',
  radley: 'Radley, Georgia, serif',
  "young-serif": '"Young Serif", Georgia, serif',
  times: '"Times New Roman", Times, "Liberation Serif", serif',
  georgia: 'Georgia, "Times New Roman", serif',
};

/** A real weight, plus an optional outline (em) to thicken it further. */
type Face = { w: number; stroke?: number };

/**
 * Regular / semibold / bold, per face. Real weights where a face has them;
 * where it doesn't — Radley and Young Serif ship one weight, Caslon, Times
 * and Georgia two — the missing steps are a hairline outline in the text's
 * own colour, which thickens the strokes evenly. A browser's faux bold would
 * smear instead. EB Garamond's 700 clogs at projector size, so its bold is
 * its semibold; Cormorant is too fine below 500 to read at a distance.
 */
const FONT_WEIGHTS: Record<Exclude<DisplayFont, "auto">, [Face, Face, Face]> = {
  sans: [{ w: 400 }, { w: 600 }, { w: 700 }],
  rounded: [{ w: 400 }, { w: 600 }, { w: 700 }],
  "eb-garamond": [{ w: 400 }, { w: 500 }, { w: 600 }],
  cormorant: [{ w: 500 }, { w: 600 }, { w: 700 }],
  caslon: [{ w: 400 }, { w: 400, stroke: 0.02 }, { w: 700 }],
  radley: [{ w: 400 }, { w: 400, stroke: 0.015 }, { w: 400, stroke: 0.03 }],
  "young-serif": [{ w: 400 }, { w: 400, stroke: 0.012 }, { w: 400, stroke: 0.025 }],
  times: [{ w: 400 }, { w: 400, stroke: 0.02 }, { w: 700 }],
  georgia: [{ w: 400 }, { w: 400, stroke: 0.02 }, { w: 700 }],
};

const WEIGHT_INDEX: Record<DisplayWeight, 0 | 1 | 2> = { regular: 0, semibold: 1, bold: 2 };

/** The typeface a look actually renders in. */
export function resolveFont(look: DisplayLook): Exclude<DisplayFont, "auto"> {
  if (look.font !== "auto") return look.font;
  return look.theme === "overhead" ? "eb-garamond" : "sans";
}

export function fontStack(look: DisplayLook): string {
  return FONT_STACK[resolveFont(look)];
}

/** Family, weight and any thickening outline, as inline style. */
export function faceStyle(look: DisplayLook): CSSProperties {
  const face = FONT_WEIGHTS[resolveFont(look)][WEIGHT_INDEX[look.weight]];
  return {
    fontFamily: fontStack(look),
    fontWeight: face.w,
    ...(face.stroke ? { WebkitTextStrokeWidth: `${face.stroke}em` } : {}),
  };
}

/** `clamp(min, n cqw, max)` scaled by the look's size, `cqw` supplied by
 * the caller (it may be a px fallback — see `useContainerUnits`). */
export function scaledClamp(
  look: DisplayLook,
  minRem: number,
  cqwValue: (n: number) => string,
  cqwN: number,
  maxRem: number,
): string {
  const k = SIZE_SCALE[look.size];
  const r = (n: number) => `${+(n * k).toFixed(3)}rem`;
  return `clamp(${r(minRem)}, ${cqwValue(+(cqwN * k).toFixed(3))}, ${r(maxRem)})`;
}

function pick<T extends string>(raw: unknown, options: { value: T }[], fallback: T): T {
  return options.some((o) => o.value === raw) ? (raw as T) : fallback;
}

/** Coerce anything (storage, an older build's payload) into a valid look. */
export function normalizeLook(raw: unknown): DisplayLook {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    theme: pick(o.theme, THEME_OPTIONS, DEFAULT_LOOK.theme),
    font: pick(o.font, FONT_OPTIONS, DEFAULT_LOOK.font),
    size: pick(o.size, SIZE_OPTIONS, DEFAULT_LOOK.size),
    weight: pick(o.weight, WEIGHT_OPTIONS, DEFAULT_LOOK.weight),
  };
}

/** An item's stored look (JSON), or null for "use the default". */
export function parseLook(json: string | null | undefined): DisplayLook | null {
  if (!json) return null;
  try {
    return normalizeLook(JSON.parse(json));
  } catch {
    return null;
  }
}

/**
 * The look a setlist item is shown in: its own, else scripture's for a
 * passage, else the default.
 */
export function lookForItem(
  base: DisplayLook,
  scripture: DisplayLook | null,
  item: { kind?: string; look?: string | null } | null | undefined,
): DisplayLook {
  return parseLook(item?.look) ?? (item?.kind === "scripture" ? scripture : null) ?? base;
}

// --- context -----------------------------------------------------------------

export const DisplayLookContext = createContext<DisplayLook>(DEFAULT_LOOK);

export function useStageLook(): DisplayLook {
  return useContext(DisplayLookContext);
}
