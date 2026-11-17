/**
 * Continuous display surface: the whole song as one column of lyrics,
 * scrolled so the words being sung sit on an anchor line a little above
 * centre, with fades at the top and bottom edges.
 *
 * Holds no presentation logic, like `SlideStage`: the parent supplies the
 * song's slides and a `FlowAnchor` (the AI's track, or a slide someone
 * picked). Motion runs in a `requestAnimationFrame` loop that writes the
 * transform directly — one React render per 1-3 s track update, not sixty a
 * second.
 *
 * Two kinds of motion, chosen by distance on screen rather than by the
 * track's `jump` flag, so a jump that lands nearby still scrolls:
 *   - within a screen: exponential easing toward the target;
 *   - further: a quick fade out, cut, fade in — scrolling a whole screen of
 *     lyrics past the congregation reads as noise.
 */

import { useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { StageSurface, inkStyle } from "@/components/presentation/StageSurface";
import { useContainerUnits } from "@/lib/containerUnits";
import { scaledClamp, useStageLook } from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import {
  anchorWord,
  layoutFlow,
  slideOffsets,
  type FlowAnchor,
} from "@/lib/presentation/flow";
import type { FlowView } from "@/lib/presentation/projection";
import type { BlankMode } from "@/lib/presentation/state";

interface Props {
  slides: ParsedSlide[];
  anchor: FlowAnchor;
  blankMode: BlankMode;
  /** Where the sung words sit, as a fraction of the height from the top. */
  anchorFraction?: number;
}

/** Easing time constant. Short enough to keep up with a fast chorus, long
 * enough that a correction reads as a drift, not a jerk. */
const EASE_SECONDS = 0.3;
const FADE_MS = 180;

interface MeasuredLine {
  start: number;
  end: number;
  top: number;
  height: number;
}

/** Pixel offset of a (fractional) word within the column. */
export function yOfWord(lines: MeasuredLine[], word: number): number {
  for (const line of lines) {
    if (word < line.start) return line.top; // in the gap before this line
    if (word < line.end) {
      const span = Math.max(1, line.end - line.start);
      return line.top + ((word - line.start) / span) * line.height;
    }
  }
  const last = lines[lines.length - 1];
  return last ? last.top + last.height : 0;
}

export function FlowStage({ slides, anchor, blankMode, anchorFraction = 0.4 }: Props) {
  const { ref: outerRef, cqw } = useContainerUnits<HTMLDivElement>();
  const columnRef = useRef<HTMLDivElement | null>(null);
  const look = useStageLook();
  const layout = useMemo(() => layoutFlow(slides), [slides]);
  const offsets = useMemo(() => slideOffsets(slides), [slides]);

  const anchorRef = useRef(anchor);
  anchorRef.current = anchor;
  const offsetsRef = useRef(offsets);
  offsetsRef.current = offsets;
  const linesRef = useRef<MeasuredLine[]>([]);
  //: Current translate in px; null until the first frame places it.
  const currentY = useRef<number | null>(null);
  const fading = useRef(false);
  const blankRef = useRef(blankMode);
  blankRef.current = blankMode;

  // Measure every line's position in the column; redo it whenever the text
  // or the size changes (fonts load, the window is resized, a pane opens).
  useLayoutEffect(() => {
    const column = columnRef.current;
    if (!column) return;
    const measure = () => {
      const out: MeasuredLine[] = [];
      column.querySelectorAll<HTMLElement>("[data-flow-line]").forEach((el) => {
        out.push({
          start: Number(el.dataset.start),
          end: Number(el.dataset.end),
          top: el.offsetTop,
          height: el.offsetHeight,
        });
      });
      linesRef.current = out;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(column);
    return () => ro.disconnect();
  }, [layout, look]);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const outer = outerRef.current;
      const column = columnRef.current;
      const lines = linesRef.current;
      if (!outer || !column || lines.length === 0 || fading.current) return;

      const word = anchorWord(anchorRef.current, offsetsRef.current, Date.now());
      const target = outer.clientHeight * anchorFraction - yOfWord(lines, word);
      const current = currentY.current;
      if (current === null) {
        currentY.current = target;
      } else if (Math.abs(target - current) > outer.clientHeight * 0.9) {
        fading.current = true;
        column.style.opacity = "0";
        window.setTimeout(() => {
          currentY.current = target;
          column.style.transform = `translate3d(0, ${target}px, 0)`;
          // React owns opacity for blanking; don't un-blank behind its back.
          column.style.opacity = blankRef.current === "none" ? "1" : "0";
          fading.current = false;
        }, FADE_MS);
        return;
      } else {
        currentY.current = current + (target - current) * (1 - Math.exp(-dt / EASE_SECONDS));
      }
      column.style.transform = `translate3d(0, ${currentY.current}px, 0)`;
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [anchorFraction, outerRef]);

  // Blanking hides the column rather than unmounting it, so the measured
  // lines and the scroll position survive a blank. The edge fade masks only
  // this viewport, so it fades into whatever backdrop the look paints.
  const hidden = blankMode === "black" || blankMode === "white";
  const edge = "linear-gradient(to bottom, transparent 0%, black 14%, black 86%, transparent 100%)";
  return (
    <StageSurface blankMode={blankMode}>
      <div
        ref={outerRef}
        data-testid="flow-stage"
        className="relative h-full w-full overflow-hidden"
        style={{ containerType: "inline-size", maskImage: edge, WebkitMaskImage: edge }}
      >
        <div
          ref={columnRef}
          className="absolute inset-x-0 top-0 px-12 text-center font-bold leading-tight will-change-transform"
          style={{
            ...inkStyle(look),
            fontSize: scaledClamp(look, 1.75, cqw, 4.2, 4.5),
            transition: `opacity ${FADE_MS}ms ease`,
            opacity: hidden ? 0 : 1,
          }}
        >
          <div className="mx-auto" style={{ maxWidth: cqw(90) }}>
            {layout.map((slide) => (
              // One blank line between slides: the gap the column parks in
              // while the band plays between sections.
              <div key={slide.slideIdx} style={{ marginBottom: "1.25em" }}>
                {slide.lines.map((line, i) => (
                  <div
                    key={i}
                    data-flow-line=""
                    data-start={line.start}
                    data-end={line.end}
                  >
                    {line.text || " "}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </StageSurface>
  );
}

/**
 * The `lyrics` slot for `ScriptureStage`: a `FlowStage` in continuous mode,
 * nothing (so the plain `SlideStage`) otherwise. Keyed on the song, so a new
 * song mounts a fresh column that places itself instead of scrolling from
 * the last one.
 */
export function flowLyrics(flow: FlowView | null | undefined, blankMode: BlankMode) {
  if (!flow) return undefined;
  return (
    <FlowStage
      key={flow.itemIdx}
      slides={flow.slides}
      anchor={flow.anchor}
      blankMode={blankMode}
    />
  );
}
