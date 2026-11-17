/**
 * Live presentation runner. Wraps the pure controller hook with:
 *   - global keyboard handling (PRESENTATION_KEYMAP)
 *   - fullscreen entry/exit (Tauri or browser)
 *   - blank-mode rendering (SlideStage)
 *   - auto-hide context bar (BottomBar), which also holds the AI status and
 *     exit — nothing else is drawn over the lyrics
 *   - last-session persistence on every state change
 *
 * Closing this view returns to whatever the parent rendered before
 * (typically the setlist builder).
 */

import { useCallback, useMemo, useRef, useState } from "react";

import { addSetlistItem, getSetlist, reorderSetlist, setSetlistItemKey, type SetlistDetail } from "@/lib/tauri";
import { buildContext } from "@/lib/presentation/state";
import { usePresentationController } from "@/lib/presentation/hook";
import {
  useLiftBlankOnMove,
  usePresentationKeymap,
  usePresentationSession,
} from "@/lib/presentation/usePresentationSession";
import { buildSetlistContent } from "@/lib/setlistContent";
import { useDisplayPref } from "@/lib/displayPrefs";
import { DisplayLookContext, useScreenLook } from "@/lib/displayLook";
import { anchorForItem } from "@/lib/presentation/flow";
import { useBandHost } from "@/lib/band/useBandHost";
import { BandScreensButton } from "@/components/band/BandScreensButton";
import { useKeepAwake } from "@/lib/presentation/useKeepAwake";
import {
  useHumanActions,
  useLiveScripture,
  usePreparedScripture,
  useScriptureOnScreen,
} from "@/lib/scripture/liveScripture";
import { preparedSlideFor, useLongPassageAsReading, useReadingOnScreen } from "@/lib/scripture/prepared";
import { flowSlides } from "@/lib/serviceItems";
import { useAutoBlank } from "@/lib/presentation/useAutoBlank";
import { useScriptureSettings } from "@/lib/scripture/useScriptureSettings";
import { isAiEnabled, serviceStartAiMode } from "@/lib/aiPrefs";
import { AIStatusPill } from "./AIStatusPill";
import { AiHealthCorner, AiHealthNotice, AiHealthProvider } from "./AiHealthIndicator";
import { AudioWaveformIndicator } from "./AudioWaveformIndicator";
import { BottomBar } from "./BottomBar";
import { ScriptureStage } from "@/components/scripture/ScriptureStage";
import { flowLyrics } from "./FlowStage";

interface Props {
  setlist: SetlistDetail;
  onExit: () => void;
  /** Optional resume position, e.g. coming from "Continue last service". */
  initialPosition?: { itemIdx: number; slideIdx: number };
  /** When true, the sidecar records audio + slide changes for this service. */
  recordingEnabled?: boolean;
}

export function PresentationView({
  setlist,
  onExit,
  initialPosition,
  recordingEnabled = false,
}: Props) {
  // Operator-style local copy so an off-setlist import can grow the set
  // without remounting the view. Memoized ctx still keys off this object.
  const [detail, setDetail] = useState(setlist);
  const ctx = useMemo(() => buildContext(detail), [detail]);
  const { state, actions, derived } = usePresentationController(ctx, { initialPosition });
  const setlistContent = useMemo(() => buildSetlistContent(detail), [detail]);

  // The AI health corner is for a service meant to have the AI; with the AI
  // switched off beforehand, off is what was asked for. Read once, at start.
  const [aiEnabled] = useState(isAiEnabled);
  const displayPref = useDisplayPref();
  const itemKinds = useMemo(() => ctx.items.map((i) => i.kind), [ctx.items]);
  const { recording, liveAiMode, displayMode, flowAnchor } = usePresentationSession({
    itemKinds,
    screen: "single",
    displayPref,
    slidesFor: (i) => {
      const item = ctx.items[i];
      return item && flowSlides(item.kind, item.slides);
    },
    setlistId: detail.id,
    setlistContent,
    recordingEnabled,
    itemIdx: state.itemIdx,
    slideIdx: state.slideIdx,
    jumpTo: actions.jumpTo,
    itemCount: ctx.items.length,
    onSetlistMutated: setDetail,
    // On unless the AI is switched off beside "Start service" (or in
    // Settings). The pill in the bottom bar switches it for this service.
    initialAiMode: serviceStartAiMode(),
  });

  // A passage put up live, by hand or out loud. With none up, `ScriptureStage`
  // renders the bare `SlideStage`.
  const scriptureSettings = useScriptureSettings();
  const onScreen = useScriptureOnScreen(scriptureSettings);
  // The item on screen may have its own style for this service; a passage
  // put up full screen wears scripture's (`useScreenLook`).
  const look = useScreenLook(ctx.items[state.itemIdx], onScreen.fullscreen && onScreen.passage !== null);
  // What the keyboard and the band leader drive: a person moving the slide
  // takes a full-screen passage down. The AI, fed `actions`, does not.
  const humanActions = useHumanActions(actions);
  // The screen blanks while nobody is singing, as well as when asked to.
  const screen = useAutoBlank(state.blankMode, liveAiMode === "active", useLiveScripture() !== null);

  // Nobody may be at the laptop (the leader drives from their own device), so
  // don't let the display — and the projector — go to sleep.
  useKeepAwake();

  // The leader's "add a song": insert it right after the current one, and
  // re-sync the AI so it can follow the new song. Mirrors the operator view.
  const itemIdxRef = useRef(state.itemIdx);
  itemIdxRef.current = state.itemIdx;
  const itemsRef = useRef(detail.items);
  itemsRef.current = detail.items;
  const addSongHere = useCallback(
    async (songId: string): Promise<string | null> => {
      try {
        const newItemId = await addSetlistItem(detail.id, songId);
        const ids = itemsRef.current.map((i) => i.id);
        ids.splice(Math.min(itemIdxRef.current + 1, ids.length), 0, newItemId);
        await reorderSetlist(detail.id, ids);
        const fresh = await getSetlist(detail.id);
        if (fresh) {
          setDetail(fresh);
          recording.updateSetlistContent(buildSetlistContent(fresh), itemIdxRef.current);
        }
        return newItemId;
      } catch (err) {
        console.error("leader add song failed:", err);
        return null;
      }
    },
    [detail.id, recording],
  );

  // The leader's key change, kept with the setlist like one from the badge.
  const saveKey = useCallback(
    (itemId: string, key: string | null) => {
      setDetail((d) => ({ ...d, items: d.items.map((it) => (it.id === itemId ? { ...it, song_key: key } : it)) }));
      setSetlistItemKey(detail.id, itemId, key).catch((err) => console.error("saving the key failed:", err));
    },
    [detail.id],
  );

  // Band screens keep following in single-screen mode too, if they're on —
  // and the leader can run the service from theirs: song, slide, blank, add.
  const band = useBandHost({
    ctx,
    itemIdx: state.itemIdx,
    slideIdx: state.slideIdx,
    blankMode: state.blankMode,
    jumpTo: humanActions.jumpTo,
    addSong: addSongHere,
    toggleBlank: actions.toggleBlank,
    setKey: saveKey,
  });

  // The arrow keys and the drawer's slide list: moving the slide also lifts
  // a blank screen.
  const operatorActions = useLiftBlankOnMove(humanActions, state.blankMode);
  // A passage asked for (typed or out loud) that the service already has
  // prepared: go to those slides, as a person moving the screen would.
  // One too long for a screen joins the service as a reading after the
  // current item, so it shows a screen at a time and can be followed.
  const longPassageAsReading = useLongPassageAsReading({
    setlistId: detail.id,
    items: ctx.items,
    itemIdx: state.itemIdx,
    jumpTo: operatorActions.jumpTo,
    reload: async () => {
      const fresh = await getSetlist(detail.id);
      if (!fresh) return;
      setDetail(fresh);
      recording.updateSetlistContent(buildSetlistContent(fresh), itemIdxRef.current);
    },
  });
  usePreparedScripture((passage) => {
    const at = preparedSlideFor(ctx.items, passage.reference, state.itemIdx);
    if (!at) return longPassageAsReading(passage);
    operatorActions.jumpTo(at.itemIdx, at.slideIdx);
    return true;
  });
  useReadingOnScreen(ctx.items, state.itemIdx, state.slideIdx, onScreen.passage !== null);
  usePresentationKeymap(operatorActions, onExit);

  return (
    <div
      data-testid="presentation-view"
      className="relative h-screen w-screen overflow-hidden bg-black"
    >
      {/* The AI starts on here, so off counts as a problem worth an X. */}
      <AiHealthProvider aiOffIsProblem>
        <DisplayLookContext.Provider value={look}>
          <ScriptureStage
            slide={derived.currentSlide}
            blankMode={screen.blankMode}
            passage={onScreen.passage}
            settings={onScreen.settings}
            fullscreen={onScreen.fullscreen}
            lyrics={flowLyrics(
              displayMode === "continuous"
                ? {
                    itemIdx: state.itemIdx,
                    slides: flowSlides(ctx.items[state.itemIdx]?.kind, ctx.items[state.itemIdx]?.slides ?? []),
                    anchor: anchorForItem(flowAnchor, state.itemIdx, state.slideIdx),
                  }
                : null,
              screen.blankMode,
            )}
          />
        </DisplayLookContext.Provider>
        <BottomBar
          items={ctx.items}
          currentItemIdx={state.itemIdx}
          currentSlideIdx={state.slideIdx}
          onJump={operatorActions.jumpTo}
          notice={aiEnabled && <AiHealthNotice className="mb-3" />}
          toolbar={
            <>
              <AIStatusPill />
              {liveAiMode !== "off" && <AudioWaveformIndicator />}
              <BandScreensButton band={band} placement="above" />
              <button
                type="button"
                onClick={(e) => {
                  e.currentTarget.blur();
                  actions.toggleBlank("black");
                }}
                className={
                  state.blankMode === "black"
                    ? "ml-auto rounded-md bg-white px-3 py-1 text-xs font-medium text-black"
                    : "ml-auto rounded-md border border-white/20 px-3 py-1 text-xs text-white/80 transition-colors hover:bg-white/10 hover:text-white"
                }
                title={screen.auto ? "Blanked: nobody is singing (B to blank by hand)" : "Blank screen (B)"}
              >
                {screen.auto ? "Blank (auto)" : "Blank"}
              </button>
              <button
                type="button"
                onClick={onExit}
                aria-label="Exit presentation"
                className="rounded-md px-3 py-1 text-xs text-white/60 transition-colors hover:bg-white/10 hover:text-white"
              >
                Exit (Esc)
              </button>
            </>
          }
        />
        {aiEnabled && <AiHealthCorner />}
      </AiHealthProvider>
    </div>
  );
}
