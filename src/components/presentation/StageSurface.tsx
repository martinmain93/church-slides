/**
 * The backdrop a lyric stage draws on, per `DisplayLook` theme — shared by
 * `SlideStage` and `FlowStage` so both modes look the same.
 *
 *   - dark: black, white text (what the stages always rendered);
 *   - light: white, black text;
 *   - overhead: an overhead projector's lit transparency — an off-white pane
 *     with softly cut corners on a black wall, brightest in the middle where
 *     the lamp sits, a gentle glow and a pixel or two of blue/amber colour
 *     fringing at the edge of the light, and faded-toner text with the same
 *     faint fringe.
 *
 * Blanking: "black" puts the whole screen out (on the overhead, the lamp
 * off); "white" is an empty lit screen — pure white, or on the overhead the
 * bare glass. Children stay mounted either way; the stages decide whether to
 * show their text, and `FlowStage` relies on staying mounted.
 */

import type { CSSProperties, ReactNode } from "react";

import { useContainerUnits } from "@/lib/containerUnits";
import { faceStyle, useStageLook, type DisplayLook } from "@/lib/look";
import type { BlankMode } from "@/lib/presentation/state";

interface Props {
  blankMode: BlankMode;
  children: ReactNode;
}

const FADE = "opacity 250ms ease, background-color 250ms ease";

/** The overhead's warm film: brightest in the middle, browning at the edges. */
const OVERHEAD_FILM = "radial-gradient(ellipse 75% 75% at 50% 50%, #f6f1e2 0%, #eee6d1 60%, #ddd2b6 100%)";

/** A slide thumbnail's background in the same theme as the real screen —
 * the flat version, without the lamp, glow and fringing. */
export function stageThumbnailStyle(look: DisplayLook): CSSProperties {
  if (look.theme === "light") return { backgroundColor: "#ffffff" };
  if (look.theme === "overhead") return { background: OVERHEAD_FILM };
  return { backgroundColor: "#09090b" };
}

/** Text colour, face and fringe for a look — spread onto the lyric element. */
export function inkStyle(look: DisplayLook): CSSProperties {
  const face = faceStyle(look);
  switch (look.theme) {
    case "light":
      return { ...face, color: "#0d0d0d" };
    case "overhead":
      return {
        ...face,
        // Faded toner: a warm charcoal rather than black, as a transparency
        // reads once the lamp has washed through it. Solid, not translucent —
        // alpha would double up where an outline-thickened face overlaps
        // its own fill (see `faceStyle`).
        color: "#3b352c",
        // Toner on film through a cheap lens: a little blur and a hair of
        // colour either side. Sub-pixel, so it reads as texture, not effect.
        textShadow:
          "0.6px 0 0 rgba(255, 96, 40, 0.2), -0.6px 0 0 rgba(40, 110, 255, 0.2), 0 0 1.5px rgba(40, 32, 20, 0.4)",
      };
    default:
      return { ...face, color: "#ffffff" };
  }
}

export function StageSurface({ blankMode, children }: Props) {
  const look = useStageLook();
  const { ref, cqw } = useContainerUnits<HTMLDivElement>();

  if (look.theme !== "overhead") {
    const bg =
      blankMode === "white" ? "#ffffff" : blankMode === "black" ? "#000000"
      : look.theme === "light" ? "#ffffff" : "#000000";
    return (
      <div
        data-testid="stage-surface"
        data-theme={look.theme}
        className="relative h-full w-full overflow-hidden"
        style={{ backgroundColor: bg, transition: FADE }}
      >
        {children}
      </div>
    );
  }

  const lampOn = blankMode !== "black";
  return (
    <div
      ref={ref}
      data-testid="stage-surface"
      data-theme="overhead"
      className="relative h-full w-full overflow-hidden bg-black"
      style={{ containerType: "inline-size" }}
    >
      <div
        className="absolute overflow-hidden"
        style={{
          inset: "3.5% 3%",
          // Elliptical radii: the lamp's light is cut off by a rounded
          // square aperture, so the corners are bevelled, not circular.
          borderRadius: "4.5% / 8%",
          background: [
            // The lamp: a soft hot spot a little above centre.
            "radial-gradient(ellipse 55% 50% at 50% 46%, rgba(255, 254, 247, 0.95) 0%, rgba(255, 252, 240, 0.35) 45%, rgba(255, 250, 235, 0) 75%)",
            // Falloff toward the edges and a warm, not-quite-white film.
            OVERHEAD_FILM,
          ].join(", "),
          boxShadow: [
            // Chromatic fringe: blue up-left, amber down-right, 1-2px.
            "-1.5px -1px 0 0 rgba(90, 140, 255, 0.55)",
            "1.5px 1px 0 0 rgba(255, 150, 50, 0.5)",
            "inset 1px 0 0 0 rgba(255, 130, 60, 0.25)",
            "inset -1px 0 0 0 rgba(80, 130, 255, 0.25)",
            // Glow at the edge of the light: a tight bloom hugging the
            // aperture, and a wide faint spill onto the wall around it.
            `0 0 ${cqw(0.8)} ${cqw(0.15)} rgba(255, 246, 222, 0.28)`,
            `0 0 ${cqw(4)} ${cqw(1)} rgba(255, 238, 200, 0.12)`,
            // The edge of the pane itself glows faintly before the
            // vignette takes over.
            `inset 0 0 ${cqw(0.6)} rgba(255, 252, 238, 0.55)`,
            // Vignette inside the pane.
            `inset 0 0 ${cqw(5)} rgba(110, 88, 48, 0.22)`,
          ].join(", "),
          opacity: lampOn ? 1 : 0,
          transition: FADE,
        }}
      >
        {children}
      </div>
    </div>
  );
}
