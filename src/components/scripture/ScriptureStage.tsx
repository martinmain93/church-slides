/**
 * `SlideStage` with a scripture pane beside it — the composition seam.
 *
 * This exists so `SlideStage` stays what it was: a pure lyric surface with no
 * knowledge of scripture. Passing `cue: null` (which is what happens whenever
 * the feature is off, or a song has no scriptures) renders `SlideStage`
 * *alone*, in the same single-cell grid it would occupy by itself, so the
 * feature-off path is not a different code path — it is the same one with
 * nothing in the second region.
 *
 * The grid is the reason this is a component rather than a sibling overlay:
 * the lower third takes height away from the lyrics, and `SlideStage` sizes
 * its text in container units against whatever region it lands in, so it
 * reflows without being told the pane exists.
 *
 * Both regions wear the display look in context — the same style as the
 * lyrics, or scripture's own when one is set.
 */

import type { ReactNode } from "react";

import { SlideStage } from "@/components/presentation/SlideStage";
import { StageSurface, stageThumbnailStyle } from "@/components/presentation/StageSurface";
import { ScripturePane } from "@/components/scripture/ScripturePane";
import { useStageLook } from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import type { BlankMode } from "@/lib/presentation/state";
import type { ScriptureSettings } from "@/lib/scripture/settings";
import type { ScripturePassage } from "@/types/generated";

/** Beside the lyrics, a passage is a lower third: it needs no lyric reflow,
 * and it is what broadcast does for exactly this. */
const LOWER_THIRD = "26%";

export interface ScriptureStageProps {
  slide: ParsedSlide | undefined;
  blankMode: BlankMode;
  /** The passage to show, or null for none. */
  passage: ScripturePassage | null;
  /** Whether to show the reference and the translation. Position, size and
   * font in it are no longer read: the pane wears the display look. */
  settings: ScriptureSettings;
  /** Scripture takes the whole screen — a `show_scripture` tool call, or an
   * operator putting a verse up deliberately. Lyrics are hidden. */
  fullscreen?: boolean;
  /** The lyric surface, when it is not the plain `SlideStage` — continuous
   * mode's `FlowStage`. Lands in the same region, so the pane still works. */
  lyrics?: ReactNode;
}

export function ScriptureStage({
  slide,
  blankMode,
  passage,
  settings,
  fullscreen = false,
  lyrics,
}: ScriptureStageProps) {
  const look = useStageLook();
  // Blanking wins over everything: if the operator has hit the blank key, a
  // verse must not stay lit on an otherwise black screen.
  const showPane = passage !== null && blankMode === "none";

  if (fullscreen && showPane) {
    return (
      <StageSurface blankMode={blankMode}>
        <ScripturePane
          passage={passage}
          showReference={settings.show_reference}
          showTranslation={settings.show_translation}
          fullscreen
        />
      </StageSurface>
    );
  }

  if (!showPane) {
    // Exactly what the app rendered before this feature existed.
    return lyrics ?? <SlideStage slide={slide} blankMode={blankMode} />;
  }

  return (
    <div
      data-testid="scripture-stage"
      className="grid h-full w-full bg-black"
      style={{ gridTemplateRows: `1fr ${LOWER_THIRD}` }}
    >
      <div className="min-h-0 min-w-0">
        {lyrics ?? <SlideStage slide={slide} blankMode={blankMode} />}
      </div>
      <div className="min-h-0 min-w-0 border-t border-white/10" style={stageThumbnailStyle(look)}>
        <ScripturePane
          passage={passage}
          showReference={settings.show_reference}
          showTranslation={settings.show_translation}
        />
      </div>
    </div>
  );
}
