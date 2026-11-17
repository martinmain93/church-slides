/**
 * Pure slide-rendering surface. Bold centered text, auto-fitted to the
 * available space, on the backdrop of the current `DisplayLook` (white on
 * black by default — see `StageSurface`).
 *
 * Receives the current slide + blank-mode and renders accordingly. No
 * logic; the parent (PresentationView) owns state.
 */

import { StageSurface, inkStyle } from "@/components/presentation/StageSurface";
import { useContainerUnits } from "@/lib/containerUnits";
import { scaledClamp, useStageLook } from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import type { BlankMode } from "@/lib/presentation/state";

interface Props {
  slide: ParsedSlide | undefined;
  blankMode: BlankMode;
}

export function SlideStage({ slide, blankMode }: Props) {
  const { ref, cqw } = useContainerUnits<HTMLDivElement>();
  const look = useStageLook();
  // Either blank hides the lyrics; the surface paints the blank itself.
  const showLyrics = blankMode === "none" && slide;
  return (
    <StageSurface blankMode={blankMode}>
      {/* `container-type: inline-size` + `cqw` rather than `vw`: the lyrics
          must size to the region they occupy, not to the window. They are the
          same thing until something takes width away from them — a scripture
          pane — at which point a `vw` clamp keeps sizing for the full screen
          and overruns. Identical rendering when this is the only thing on the
          stage.

          Sized through `cqw()` rather than a Tailwind arbitrary-value class:
          on a web view without container units, that class was dropped and
          the lyrics fell to 16px. See `useContainerUnits`. */}
      <div
        ref={ref}
        className="flex h-full w-full items-center justify-center px-12 py-16"
        style={{ containerType: "inline-size" }}
      >
        {showLyrics && (
          <div
            className="text-center font-bold leading-tight"
            style={{
              ...inkStyle(look),
              maxWidth: cqw(90),
              fontSize: scaledClamp(look, 2, cqw, 5, 5),
            }}
          >
            {slide.lyrics.split("\n").map((line, i) => (
              <div key={i}>{line || " "}</div>
            ))}
          </div>
        )}
      </div>
    </StageSurface>
  );
}
