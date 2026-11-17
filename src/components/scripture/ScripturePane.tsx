/**
 * The scripture pane itself: one passage, rendered for a projector.
 *
 * Pure presentation — it receives a passage and draws it. It knows nothing
 * about slides or timing; `ScriptureStage` owns where it sits and
 * `liveScripture` owns when it changes.
 *
 * It wears the same style as the lyrics: the display look from
 * `DisplayLookContext` (colours, typeface, size, weight), which is the
 * scripture override when one is set (Settings → Scripture). There are no
 * scripture-only size or font settings any more.
 *
 * Type is sized in container units (`cqw`), so it scales to the pane, not
 * the window: a lower third and a full screen need different base sizes.
 */

import { inkStyle } from "@/components/presentation/StageSurface";
import { useContainerUnits } from "@/lib/containerUnits";
import { SIZE_SCALE, useStageLook } from "@/lib/displayLook";
import { cn } from "@/lib/utils";
import type { ScripturePassage } from "@/types/generated";

/** Verse-text size in `cqw` at the standard size, beside the lyrics (a lower
 * third) and full screen, where it is the only thing on the stage. */
const LOWER_THIRD_CQW = 1.9;
const FULLSCREEN_CQW = 4.2;

export interface ScripturePaneProps {
  passage: ScripturePassage | null;
  showReference: boolean;
  showTranslation: boolean;
  /** Full-screen scripture: centred, large, no lyrics beside it. */
  fullscreen?: boolean;
}

export function ScripturePane({
  passage,
  showReference,
  showTranslation,
  fullscreen = false,
}: ScripturePaneProps) {
  const look = useStageLook();
  // `cqw()` falls back to measured px on web views without container units.
  const { ref, cqw } = useContainerUnits<HTMLDivElement>();
  if (!passage) return null;

  const verseCqw = (fullscreen ? FULLSCREEN_CQW : LOWER_THIRD_CQW) * SIZE_SCALE[look.size];
  const ink = inkStyle(look);

  return (
    <div
      data-testid="scripture-pane"
      // `container-type: inline-size` is what makes the cqw units above mean
      // "a fraction of this pane" rather than of the window.
      className={cn(
        "flex h-full w-full flex-col justify-center gap-[0.7em] overflow-hidden px-[4%] py-[3%]",
        fullscreen && "items-center text-center",
      )}
      style={{ containerType: "inline-size", ...ink }}
      ref={ref}
    >
      {showReference && (
        <p
          className="m-0 uppercase tracking-[0.04em] opacity-70"
          style={{ fontSize: cqw(verseCqw * 0.72) }}
        >
          {passage.reference}
          {showTranslation && (
            <span
              className="ml-2 normal-case tracking-normal opacity-60"
              style={{ fontSize: cqw(verseCqw * 0.62) }}
            >
              {passage.abbreviation || passage.translation.toUpperCase()}
            </span>
          )}
        </p>
      )}
      <p className="m-0 leading-[1.35]" style={{ fontSize: cqw(verseCqw) }}>
        {passage.text}
      </p>
    </div>
  );
}
