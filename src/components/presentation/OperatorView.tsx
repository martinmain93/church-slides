/**
 * Operator control view — the "second screen" surface.
 *
 * Runs in the main window while the audience slide is projected via
 * `useAudienceProjection`, which opens the audience window by itself —
 * fullscreen on a spare monitor when there is one. The operator gets:
 *
 *   - a scrollable grid of every slide of every song, divided per song with a
 *     title/author header; click a slide to navigate, right-click to
 *     edit / duplicate / remove, drag to reorder, "Add slide" to append
 *   - a live search panel: type to filter the library; hover a result to
 *     "Add here" (after the current song) or "Add to end"
 *   - a short top bar: the AI switch, blank and exit; band screens and
 *     "Nearby" once in use; the rest (lyric display, look, audience window)
 *     in its "⋯" menu
 *
 * The presentation state machine is owned here (via `usePresentationController`)
 * exactly like `PresentationView`; this view just renders a richer control
 * surface around it. Setlist mutations persist to the DB and rebuild the
 * context in place (position preserved by item id — see `hook.ts`).
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  Maximize,
  Monitor,
  MonitorCog,
  MonitorOff,
  Music,
  Palette,
  Plus,
  Radio,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { sectionLabel, type ParsedSlide, type SectionType } from "@/lib/parseSong";
import { usePresentationController } from "@/lib/presentation/hook";
import {
  addSlide as addSlideOp,
  deleteAt,
  duplicateAt,
  insertAfter,
  editLyrics,
  editableSlides,
  moveSlide,
  persistOverride,
} from "@/lib/presentation/slideEditing";
import { buildContext } from "@/lib/presentation/state";
import { useAudienceProjection } from "@/lib/presentation/useProjection";
import { useBandHost, type BandHostApi } from "@/lib/band/useBandHost";
import { BandScreensButton, bandScreensMenuHint } from "@/components/band/BandScreensButton";
import { LiveSetStatus } from "@/components/band/LiveSetStatus";
import { PeerSyncButton } from "@/components/peerSync/PeerSyncButton";
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
import { LiveScripturePanel } from "@/components/scripture/LiveScripturePanel";
import { FEATURES } from "@/lib/features";
import { cn } from "@/lib/utils";
import {
  useLiftBlankOnMove,
  usePresentationKeymap,
  usePresentationSession,
} from "@/lib/presentation/usePresentationSession";
import { buildSetlistContent } from "@/lib/setlistContent";
import { useDisplayPref } from "@/lib/displayPrefs";
import { useItemLooks, useScreenLook, type DisplayLook } from "@/lib/displayLook";
import { DisplayLookDialog } from "./DisplayLookDialog";
import { ScreensDialog } from "./ScreensDialog";
import { monitorCount } from "@/lib/presentation/projection";
import { anchorForItem } from "@/lib/presentation/flow";
import { firstVerseLines, isBlankSlide } from "@/lib/slideSequence";
import {
  addSetlistItem,
  getSetlist,
  listChartKeys,
  listSongsSorted,
  removeSetlistItem,
  setSetlistItemKey,
  reorderSetlist,
  searchSongs,
  type SetlistDetail,
  type SetlistItemWithSong,
  type SongRow,
} from "@/lib/tauri";
import { KeyBadge } from "@/components/KeyBadge";
import { ServiceKeyBadge } from "@/components/ServiceKeyBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem as MoreItem, MenuSeparator } from "@/components/ui/Menu";
import { ResizablePanels } from "@/components/ui/ResizablePanels";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { operatorStartAiMode } from "@/lib/aiPrefs";
import { dismissAssistSuggestion, useAssistSuggestion } from "@/lib/assist";
import { AIStatusPill } from "./AIStatusPill";
import { AssistNotice, slideTile, type PlaceDescription } from "./AssistNotice";
import { AiHealthNotice, AiHealthProvider } from "./AiHealthIndicator";
import { AudioWaveformIndicator } from "./AudioWaveformIndicator";
import { SetlistSlideCard } from "@/components/setlists/SetlistSlideCard";

interface Props {
  setlist: SetlistDetail;
  onExit: () => void;
  initialPosition?: { itemIdx: number; slideIdx: number };
  recordingEnabled?: boolean;
}

const SEARCH_DEBOUNCE_MS = 180;

export function OperatorView({
  setlist: initialSetlist,
  onExit,
  initialPosition,
  recordingEnabled = false,
}: Props) {
  const toast = useToast();
  const confirm = useConfirm();
  const [detail, setDetail] = useState<SetlistDetail>(initialSetlist);
  const [chartKeys, setChartKeys] = useState<Record<string, string | null>>({});

  useEffect(() => {
    let cancelled = false;
    listChartKeys()
      .then((keys) => {
        if (!cancelled) setChartKeys(keys);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const ctx = useMemo(() => buildContext(detail), [detail]);
  const { state, actions, derived } = usePresentationController(ctx, {
    initialPosition,
  });

  // AI session — started once at mount with the initial content; re-synced
  // in place when the operator edits the setlist (see `applyMutation`).
  const initialContent = useMemo(
    () => buildSetlistContent(initialSetlist),
    [initialSetlist],
  );

  const itemKinds = useMemo(() => ctx.items.map((i) => i.kind), [ctx.items]);
  const displayPref = useDisplayPref();
  const { recording, liveAiMode, displayMode, flowAnchor } = usePresentationSession({
    itemKinds,
    screen: "operator",
    displayPref,
    slidesFor: (i) => {
      const item = ctx.items[i];
      return item && flowSlides(item.kind, item.slides);
    },
    setlistId: detail.id,
    setlistContent: initialContent,
    recordingEnabled,
    itemIdx: state.itemIdx,
    slideIdx: state.slideIdx,
    jumpTo: actions.jumpTo,
    itemCount: ctx.items.length,
    onSetlistMutated: (fresh) => {
      setDetail(fresh);
    },
    // On unless the AI is switched off beside "Start service" (or in
    // Settings), and "on" here means Assist unless the operator last chose
    // Auto. The status pill in the top bar switches it for this service.
    initialAiMode: operatorStartAiMode(),
  });

  // Slide-index refs so mutation helpers stay stable across advances —
  // otherwise every song's DndContext re-renders when the current thumb
  // changes.
  const itemIdxRef = useRef(state.itemIdx);
  itemIdxRef.current = state.itemIdx;
  const itemsRef = useRef(detail.items);
  itemsRef.current = detail.items;

  // A passage put up live, by hand or by voice.
  const scriptureSettings = useScriptureSettings();
  const onScreen = useScriptureOnScreen(scriptureSettings);
  // What the keyboard, the grid and the band leader drive. A person moving
  // the slide takes a full-screen passage down; the AI (fed `actions` above)
  // does not.
  const humanActions = useHumanActions(actions);
  // The grid and the arrow keys: moving the slide also lifts a blank screen.
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
    // Declared further down; only called later, from the handler.
    reload: () => applyMutation(),
  });
  usePreparedScripture((passage) => {
    const at = preparedSlideFor(ctx.items, passage.reference, state.itemIdx);
    if (!at) return longPassageAsReading(passage);
    operatorActions.jumpTo(at.itemIdx, at.slideIdx);
    return true;
  });
  useReadingOnScreen(ctx.items, state.itemIdx, state.slideIdx, onScreen.passage !== null);

  // AI assist: the slide the AI hears the band on, when the screen isn't
  // there. Pulses its tile; a notice when the tile is out of view.
  const suggestion = useAssistSuggestion(liveAiMode === "assist");
  const gridRef = useRef<HTMLDivElement | null>(null);
  const describePlace = useCallback(
    (itemIdx: number, slideIdx: number): PlaceDescription | null => {
      const item = ctx.items[itemIdx];
      const slide = item?.slides[slideIdx];
      if (!item || !slide || isBlankSlide(slide)) return null;
      return {
        title: item.song.title,
        section: sectionLabel(slide.section),
        firstLine: slide.lyrics.split("\n")[0]?.trim() ?? "",
      };
    },
    [ctx.items],
  );
  // Go: put the slide up, and bring its tile into view — on a jump to
  // another song it's usually off screen (that's why there was a notice).
  const onAssistGo = useCallback(
    (itemIdx: number, slideIdx: number) => {
      operatorActions.jumpTo(itemIdx, slideIdx);
      slideTile(gridRef.current, itemIdx, slideIdx)?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    },
    [operatorActions],
  );
  const onDismissSuggestion = useCallback((id: number) => {
    void dismissAssistSuggestion(id).catch(() => undefined);
  }, []);
  // The audience screen blanks while nobody is singing, as well as when asked.
  const screen = useAutoBlank(state.blankMode, liveAiMode === "active", useLiveScripture() !== null);

  // Projection: mirror the current slide to the audience window.
  // A song's or passage's own style for this service wins over scripture's,
  // which wins over the default (`lookForItem`).
  const look = useScreenLook(ctx.items[state.itemIdx], onScreen.fullscreen && onScreen.passage !== null);
  const itemLooks = useItemLooks(ctx.items);
  const currentItem = ctx.items[state.itemIdx];
  const currentSlides = currentItem && flowSlides(currentItem.kind, currentItem.slides);
  const flow = useMemo(
    () =>
      displayMode === "continuous" && currentSlides
        ? {
            itemIdx: state.itemIdx,
            slides: currentSlides,
            anchor: anchorForItem(flowAnchor, state.itemIdx, state.slideIdx),
          }
        : null,
    [displayMode, currentSlides, state.itemIdx, state.slideIdx, flowAnchor],
  );
  const payload = useMemo(
    () => ({
      slide: derived.currentSlide ?? null,
      blankMode: screen.blankMode,
      scripture: onScreen.passage,
      scriptureSettings: onScreen.settings,
      scriptureFullscreen: onScreen.fullscreen,
      flow,
      look,
    }),
    [derived.currentSlide, screen.blankMode, onScreen, flow, look],
  );
  // Opens by itself — fullscreen on the projector when there's a spare
  // monitor — so starting the service is the only thing the operator does.
  const projection = useAudienceProjection(payload, { autoOpen: true });

  // --- mutations ----------------------------------------------------------

  // Re-fetch the setlist after a DB mutation, rebuild the context (the
  // controller reconciles position by item id), and re-sync the AI matcher.
  const applyMutation = useCallback(async () => {
    const fresh = await getSetlist(detail.id);
    if (!fresh) return;
    setDetail(fresh);
    recording.updateSetlistContent(buildSetlistContent(fresh), itemIdxRef.current);
  }, [detail.id, recording]);

  const handleAddToEnd = useCallback(
    async (song: SongRow) => {
      try {
        await addSetlistItem(detail.id, song.id);
        await applyMutation();
      } catch (err) {
        toast.error(`Add failed: ${err instanceof Error ? err.message : err}`);
      }
    },
    [detail.id, applyMutation, toast],
  );

  // Insert a library song directly after the current one. Resolves to the new
  // item's id (null on failure) — the band leader's "go now" jumps to it.
  const addSongHere = useCallback(
    async (songId: string): Promise<string | null> => {
      try {
        const newItemId = await addSetlistItem(detail.id, songId);
        // It appended at the tail; move it directly after the current song.
        const ids = itemsRef.current.map((i) => i.id);
        const insertAt = Math.min(itemIdxRef.current + 1, ids.length);
        ids.splice(insertAt, 0, newItemId);
        await reorderSetlist(detail.id, ids);
        await applyMutation();
        return newItemId;
      } catch (err) {
        toast.error(`Add failed: ${err instanceof Error ? err.message : err}`);
        return null;
      }
    },
    [detail.id, applyMutation, toast],
  );
  const handleAddHere = useCallback(
    async (song: SongRow) => {
      await addSongHere(song.id);
    },
    [addSongHere],
  );

  // A song's key for this service, from its key badge or the leader's
  // transpose. Shown at once; the band screens follow the context.
  const handleKeyChange = useCallback(
    (itemId: string, key: string | null) => {
      setDetail((d) => ({ ...d, items: d.items.map((it) => (it.id === itemId ? { ...it, song_key: key } : it)) }));
      setSetlistItemKey(detail.id, itemId, key).catch((err) =>
        toast.error(`Couldn't change the key: ${err instanceof Error ? err.message : err}`),
      );
    },
    [detail.id, toast],
  );
  const onKeyChangeAt = useCallback(
    (itemIdx: number, key: string | null) => {
      const item = ctx.items[itemIdx];
      if (item) handleKeyChange(item.itemId, key);
    },
    [ctx.items, handleKeyChange],
  );

  // Band screens: lyrics + chords on the band's own devices, and the
  // leader's song palette. Inert until switched on.
  const band = useBandHost({
    ctx,
    itemIdx: state.itemIdx,
    slideIdx: state.slideIdx,
    blankMode: state.blankMode,
    jumpTo: humanActions.jumpTo,
    addSong: addSongHere,
    toggleBlank: actions.toggleBlank,
    setKey: handleKeyChange,
  });

  // The projector must not sleep mid-service, operator or not.
  useKeepAwake();

  const handleRemoveSong = useCallback(
    async (item: SetlistItemWithSong) => {
      const ok = await confirm({
        title: `Remove "${item.song?.title ?? "this song"}"?`,
        message: "Removes it from this service. The song stays in your library.",
        confirmLabel: "Remove",
        variant: "destructive",
      });
      if (!ok) return;
      try {
        await removeSetlistItem(detail.id, item.id);
        await applyMutation();
      } catch (err) {
        toast.error(`Remove failed: ${err instanceof Error ? err.message : err}`);
      }
    },
    [detail.id, applyMutation, confirm, toast],
  );

  // Slide-level edits operate on the item's override list (real slides only).
  const editSlides = useCallback(
    async (
      item: SetlistItemWithSong,
      fn: (slides: ReturnType<typeof editableSlides>) => ReturnType<typeof editableSlides>,
    ) => {
      try {
        const next = fn(editableSlides(item));
        await persistOverride(item, next);
        await applyMutation();
      } catch (err) {
        toast.error(`Edit failed: ${err instanceof Error ? err.message : err}`);
      }
    },
    [applyMutation, toast],
  );

  const onEditLyrics = useCallback(
    (itemIdx: number, overrideIdx: number, lyrics: string) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      void editSlides(item, (s) => editLyrics(s, overrideIdx, lyrics));
    },
    [editSlides],
  );
  const onDuplicate = useCallback(
    (itemIdx: number, overrideIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      void editSlides(item, (s) => duplicateAt(s, overrideIdx));
    },
    [editSlides],
  );
  // An inserted slide opens for typing once it exists. During a service it
  // isn't put on screen: an empty slide on the projector helps nobody.
  const [openForEditing, setOpenForEditing] = useState<{ itemIdx: number; overrideIdx: number } | null>(null);
  const onInsertSlide = useCallback(
    async (itemIdx: number, overrideIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      await editSlides(item, (s) => insertAfter(s, overrideIdx));
      setOpenForEditing({ itemIdx, overrideIdx: overrideIdx + 1 });
    },
    [editSlides],
  );
  const onEditingOpened = useCallback(() => setOpenForEditing(null), []);
  const onDeleteSlide = useCallback(
    (itemIdx: number, overrideIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      void editSlides(item, (s) => deleteAt(s, overrideIdx));
    },
    [editSlides],
  );
  const onAddSlide = useCallback(
    (itemIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      void editSlides(item, (s) => addSlideOp(s));
    },
    [editSlides],
  );
  const onReorder = useCallback(
    (itemIdx: number, fromOverrideIdx: number, toOverrideIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (!item) return;
      void editSlides(item, (s) => moveSlide(s, fromOverrideIdx, toOverrideIdx));
    },
    [editSlides],
  );
  const onRemoveSongAt = useCallback(
    (itemIdx: number) => {
      const item = itemsRef.current[itemIdx];
      if (item) void handleRemoveSong(item);
    },
    [handleRemoveSong],
  );

  const existingSongIds = useMemo(
    () => new Set(detail.items.map((i) => i.song_id)),
    [detail.items],
  );

  const minimapItems = useMemo(
    () =>
      ctx.items.map((it) => ({
        itemId: it.itemId,
        title: it.song.title,
        chartKey: chartKeys[it.song.id] ?? null,
        songKey: it.kind === "song" ? (it.songKey ?? null) : undefined,
      })),
    [ctx.items, chartKeys],
  );

  // Scroll the grid to a given song (the minimap's actuator role).
  const scrollToSong = useCallback((itemIdx: number) => {
    document
      .querySelector<HTMLElement>(`[data-rail-item-idx="${itemIdx}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const el = document.getElementById(`op-song-${itemIdx}`);
    const container = gridRef.current;
    if (el && container) {
      container.scrollTo({
        top: Math.max(0, el.offsetTop - 16),
        behavior: "smooth",
      });
    } else {
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  const onReorderSongs = useCallback(
    async (oldIdx: number, newIdx: number) => {
      if (oldIdx === newIdx) {
        scrollToSong(oldIdx);
        return;
      }
      const reordered = arrayMove(itemsRef.current, oldIdx, newIdx);
      setDetail((prev) => ({ ...prev, items: reordered }));
      requestAnimationFrame(() => {
        scrollToSong(newIdx);
      });
      try {
        await reorderSetlist(
          detail.id,
          reordered.map((i) => i.id),
        );
        await applyMutation();
      } catch (err) {
        toast.error(`Reorder failed: ${err instanceof Error ? err.message : err}`);
        await applyMutation();
      }
    },
    [detail.id, applyMutation, scrollToSong, toast],
  );

  // --- keyboard nav ---------------------------------------------------------

  usePresentationKeymap(operatorActions, onExit);

  // Keep the currently selected slide (and its song in the left rail) on screen
  // whenever the active position moves.
  const initialScrollRef = useRef(true);
  useEffect(() => {
    const behavior: ScrollBehavior = initialScrollRef.current ? "instant" : "smooth";
    initialScrollRef.current = false;
    slideTile(gridRef.current, state.itemIdx, state.slideIdx)?.scrollIntoView?.({
      behavior,
      block: "nearest",
    });
    document
      .querySelector<HTMLElement>(`[data-rail-item-idx="${state.itemIdx}"]`)
      ?.scrollIntoView?.({
        behavior,
        block: "nearest",
      });
  }, [state.itemIdx, state.slideIdx]);

  return (
    <div
      data-testid="operator-view"
      className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground"
    >
      <ProjectionBar
        setlistName={detail.name}
        projection={projection}
        band={band}
        aiListening={liveAiMode !== "off"}
        recording={recordingEnabled}
        onExit={onExit}
        peerSync={(openSignal) => (
          <PeerSyncButton
            setlistId={detail.id}
            setlistName={detail.name}
            onMessage={(m, kind) => (kind === "error" ? toast.error(m) : toast.success(m))}
            openSignal={openSignal}
            hideWhenAlone
            align="right"
          />
        )}
      />

      {/* The operator's own screen only; the audience window renders
          AudienceView, which has none of this. The AI starts off here, so
          only problems while it's on are worth saying. */}
      <AiHealthProvider aiOffIsProblem={false}>
        <AiHealthNotice className="mx-4 mt-2 text-foreground" />
      </AiHealthProvider>

      <ResizablePanels
        className="flex-1"
        flexIndex={1}
        initialSizes={[240, 0, 340]}
        minSizes={[180, 320, 260]}
        storageKey="setlist_workspace_panels_v1"
      >
        <div className="flex h-full min-h-0 flex-col bg-muted/20">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Service
            </span>
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {minimapItems.length}
            </span>
          </div>
          {/* Song minimap — scroll follower + actuator */}
          <div className="min-h-0 flex-1">
            <SongMinimap
              items={minimapItems}
              currentItemIdx={state.itemIdx}
              onSelect={scrollToSong}
              onKeyChange={handleKeyChange}
              onReorderSongs={onReorderSongs}
              onRemoveSong={onRemoveSongAt}
            />
          </div>
          <div className="border-t border-border bg-muted/20 p-2">
            <Button
              variant={state.blankMode === "black" ? "default" : "outline"}
              onClick={() => actions.toggleBlank("black")}
              aria-pressed={state.blankMode === "black"}
              className="h-14 w-full flex-col gap-0 whitespace-normal text-sm font-semibold leading-tight"
              title={
                screen.auto
                  ? "Blanked automatically: nobody is singing. Click (or B) to blank by hand."
                  : "Blank screen on/off (B)"
              }
            >
              Show Blank Slide
              {screen.auto && (
                <span className="text-[11px] font-normal text-muted-foreground">
                  Blank now: nobody singing
                </span>
              )}
            </Button>
          </div>
        </div>

        {/* Slide grid */}
        <div className="relative h-full">
          <div ref={gridRef} className="h-full overflow-y-auto px-6 py-4">
            {ctx.items.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                This service is empty. Search on the right to add songs.
              </div>
            ) : (
              ctx.items.map((item, itemIdx) => (
                <SongBlock
                  key={item.itemId}
                  itemIdx={itemIdx}
                  showSections={item.kind === "song"}
                  title={item.song.title}
                  author={item.song.author}
                  chartKey={chartKeys[item.song.id] ?? null}
                  songKey={item.kind === "song" ? (item.songKey ?? null) : undefined}
                  onKeyChange={onKeyChangeAt}
                  look={itemLooks[itemIdx] ?? look}
                  slides={item.slides}
                  currentSlideIdx={
                    state.itemIdx === itemIdx ? state.slideIdx : null
                  }
                  suggestedSlideIdx={suggestion?.itemIdx === itemIdx ? suggestion.slideIdx : null}
                  suggestedTier={suggestion?.itemIdx === itemIdx ? suggestion.tier : null}
                  onNavigate={operatorActions.jumpTo}
                  onEditLyrics={onEditLyrics}
                  onDuplicate={onDuplicate}
                  onInsertSlide={onInsertSlide}
                  openSlideIdx={openForEditing?.itemIdx === itemIdx ? openForEditing.overrideIdx : null}
                  onEditingOpened={onEditingOpened}
                  onDeleteSlide={onDeleteSlide}
                  onAddSlide={onAddSlide}
                  onReorder={onReorder}
                  onRemoveSong={onRemoveSongAt}
                />
              ))
            )}
          </div>
          <AssistNotice
            suggestion={suggestion}
            gridRef={gridRef}
            describe={describePlace}
            onGo={onAssistGo}
            onDismiss={onDismissSuggestion}
          />
        </div>

        {/* Search panel: songs, and verses to put up now */}
        <RightPanel
          songs={
            <SearchPanel
              existingSongIds={existingSongIds}
              chartKeys={chartKeys}
              onAddHere={handleAddHere}
              onAddToEnd={handleAddToEnd}
            />
          }
          translation={scriptureSettings.translation}
        />
      </ResizablePanels>
    </div>
  );
}

// --- projection / top bar ----------------------------------------------------

/**
 * Only what an operator reaches for mid-service stays in the bar: the AI
 * switch, blank and exit. Band screens and "Nearby" appear once they're in
 * use; the audience-screen buttons only while that screen still needs a hand
 * (no spare monitor was found, or it was closed). Everything else is in "⋯".
 */
function ProjectionBar({
  setlistName,
  projection,
  band,
  aiListening,
  recording,
  onExit,
  peerSync,
}: {
  setlistName: string;
  projection: ReturnType<typeof useAudienceProjection>;
  band: BandHostApi;
  /** The AI is switched on — only then is the microphone level worth a glance. */
  aiListening: boolean;
  /** This service is being recorded (Settings → Developer tools). */
  recording: boolean;
  onExit: () => void;
  /** Send/get setlists to another operator on the network. */
  peerSync: (openSignal: number) => React.ReactNode;
}) {
  const { open, placed, openAudience, closeAudience, toggleFullscreen } = projection;
  const [lookOpen, setLookOpen] = useState(false);
  const [bandSignal, setBandSignal] = useState(0);
  const [peerSignal, setPeerSignal] = useState(0);
  const [screensOpen, setScreensOpen] = useState(false);
  // Only worth offering with somewhere else to put a screen.
  const [displays, setDisplays] = useState(1);
  useEffect(() => {
    void monitorCount().then(setDisplays);
  }, [open]);
  const toast = useToast();

  return (
    <header className="flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-2">
      <span className="min-w-0 truncate text-sm font-semibold" title={setlistName}>
        {setlistName || "Untitled service"}
      </span>

      {!open ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => void openAudience()}
          className="h-8 shrink-0 gap-1"
          title="Show the slides on the projector"
        >
          <Monitor className="size-4" />
          Open audience screen
        </Button>
      ) : (
        !placed && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void toggleFullscreen()}
            className="h-8 shrink-0 gap-1"
            title="Drag the audience window onto the projector first, then make it fullscreen"
          >
            <Maximize className="size-4" />
            Audience fullscreen
          </Button>
        )
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {recording && (
          <span
            className="flex items-center gap-1.5 rounded-full border border-red-500/40 px-2 py-0.5 text-xs text-red-400"
            title="This service's audio is being saved on this computer. Turn it off in Settings → Other → Developer tools."
          >
            <span className="size-2 rounded-full bg-red-500" aria-hidden />
            Recording
          </span>
        )}
        <LiveSetStatus hosted={band.hosted} />
        <AIStatusPill operator />
        {aiListening && <AudioWaveformIndicator />}
        {peerSync(peerSignal)}
        <BandScreensButton band={band} align="right" hideButton openSignal={bandSignal} />
        <Menu label="More options">
          <MoreItem icon={<Palette />} onSelect={() => setLookOpen(true)}>
            Display look…
          </MoreItem>
          <MenuSeparator />
          <MoreItem
            icon={<Music />}
            onSelect={() => setBandSignal((n) => n + 1)}
            hint={bandScreensMenuHint(band)}
          >
            Band screens…
          </MoreItem>
          <MoreItem icon={<Radio />} onSelect={() => setPeerSignal((n) => n + 1)}>
            Send or get a setlist…
          </MoreItem>
          <MenuSeparator />
          {open ? (
            <>
              <MoreItem icon={<Maximize />} onSelect={() => void toggleFullscreen()}>
                Audience screen fullscreen on/off
              </MoreItem>
              <MoreItem icon={<MonitorOff />} onSelect={() => void closeAudience()}>
                Close audience screen
              </MoreItem>
            </>
          ) : (
            <MoreItem icon={<Monitor />} onSelect={() => void openAudience()}>
              Open audience screen
            </MoreItem>
          )}
          {displays > 1 && (
            <MoreItem icon={<MonitorCog />} onSelect={() => setScreensOpen(true)}>
              Screens…
            </MoreItem>
          )}
        </Menu>
        <Button variant="ghost" size="sm" onClick={onExit} className="h-8 gap-1" title="End the service (Esc)">
          <X className="size-4" />
          Stop presenting
        </Button>
      </div>
      {lookOpen && <DisplayLookDialog onClose={() => setLookOpen(false)} />}
      {screensOpen && <ScreensDialog onClose={() => setScreensOpen(false)} onError={(m) => toast.error(m)} />}
    </header>
  );
}

// --- song minimap ------------------------------------------------------------

const SongMinimap = memo(function SongMinimap({
  items,
  currentItemIdx,
  onSelect,
  onKeyChange,
  onReorderSongs,
  onRemoveSong,
}: {
  items: { itemId: string; title: string; chartKey: string | null; songKey?: string | null }[];
  currentItemIdx: number;
  onSelect: (itemIdx: number) => void;
  onKeyChange: (itemId: string, key: string | null) => void;
  onReorderSongs: (oldIdx: number, newIdx: number) => void;
  onRemoveSong: (itemIdx: number) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    const oldIdx = items.findIndex((i) => i.itemId === active.id);
    if (oldIdx < 0) return;
    if (!over || active.id === over.id) {
      onSelect(oldIdx);
      return;
    }
    const newIdx = items.findIndex((i) => i.itemId === over.id);
    if (newIdx < 0) return;
    onReorderSongs(oldIdx, newIdx);
  }

  return (
    <nav className="flex h-full flex-col overflow-y-auto px-1 py-1">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext
          items={items.map((i) => i.itemId)}
          strategy={verticalListSortingStrategy}
        >
          <ul className="space-y-0.5">
            {items.map(({ itemId, title, chartKey, songKey }, idx) => (
              <SortableMinimapRow
                key={itemId}
                itemId={itemId}
                idx={idx}
                title={title}
                chartKey={chartKey}
                songKey={songKey}
                onKeyChange={(key) => onKeyChange(itemId, key)}
                active={idx === currentItemIdx}
                onSelect={() => onSelect(idx)}
                onRemove={() => onRemoveSong(idx)}
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
    </nav>
  );
});

function SortableMinimapRow({
  itemId,
  idx,
  title,
  chartKey,
  songKey,
  onKeyChange,
  active,
  onSelect,
  onRemove,
}: {
  itemId: string;
  idx: number;
  title: string;
  chartKey: string | null;
  /** Set for a song (null: its own key); undefined for a passage or text. */
  songKey?: string | null;
  onKeyChange: (key: string | null) => void;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  // The whole row is the drag handle; a click still selects, since a drag
  // needs a few pixels of movement first.
  const { listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: itemId });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <li ref={setNodeRef} style={style} data-rail-item-idx={idx}>
      <div
        {...listeners}
        className={cn(
          "group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors",
          isDragging && "cursor-grabbing",
          active
            ? "bg-accent font-semibold text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
        )}
      >
        <button
          type="button"
          title={title}
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <span className="w-4 shrink-0 tabular-nums text-[11px] opacity-60">
            {(idx + 1).toString().padStart(2, "0")}
          </span>
          <span className="truncate">{title}</span>
        </button>
        {songKey !== undefined && (
          <ServiceKeyBadge originalKey={chartKey} serviceKey={songKey} onChange={onKeyChange} />
        )}
        <button
          type="button"
          onClick={onRemove}
          title="Remove song from service"
          aria-label="Remove song from service"
          className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
        >
          <X className="size-3.5" />
        </button>
      </div>
    </li>
  );
}

// --- per-song block ----------------------------------------------------------

interface SongBlockProps {
  itemIdx: number;
  title: string;
  author: string | null;
  chartKey: string | null;
  /** Set for a song (null: its own key); undefined for a passage or text. */
  songKey?: string | null;
  onKeyChange: (itemIdx: number, key: string | null) => void;
  look: DisplayLook;
  slides: ParsedSlide[];
  currentSlideIdx: number | null;
  /** AI assist: the slide in this song the AI hears the band on, when the
   * screen isn't there, and how pressing the suggestion is. */
  suggestedSlideIdx: number | null;
  suggestedTier: "next" | "place" | null;
  onNavigate: (itemIdx: number, slideIdx: number) => void;
  onEditLyrics: (itemIdx: number, overrideIdx: number, lyrics: string) => void;
  onDuplicate: (itemIdx: number, overrideIdx: number) => void;
  onInsertSlide: (itemIdx: number, overrideIdx: number) => void;
  /** The slide (override index) an insert just added, to open for typing. */
  openSlideIdx: number | null;
  onEditingOpened: () => void;
  onDeleteSlide: (itemIdx: number, overrideIdx: number) => void;
  onAddSlide: (itemIdx: number) => void;
  onReorder: (itemIdx: number, fromOverrideIdx: number, toOverrideIdx: number) => void;
  onRemoveSong: (itemIdx: number) => void;
  /** Songs label slides with their sections; a passage or text item doesn't. */
  showSections: boolean;
}

const SongBlock = memo(function SongBlock({
  itemIdx,
  showSections,
  title,
  author,
  chartKey,
  songKey,
  onKeyChange,
  look,
  slides,
  currentSlideIdx,
  suggestedSlideIdx,
  suggestedTier,
  onNavigate,
  onEditLyrics,
  onDuplicate,
  onInsertSlide,
  openSlideIdx,
  onEditingOpened,
  onDeleteSlide,
  onAddSlide,
  onReorder,
  onRemoveSong,
}: SongBlockProps) {
  // Real (editable) slides exclude the synthetic leading blank at index 0.
  const realSlides = slides.filter((s) => !isBlankSlide(s));
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = realSlides.findIndex((s) => s.id === active.id);
    const to = realSlides.findIndex((s) => s.id === over.id);
    if (from < 0 || to < 0) return;
    onReorder(itemIdx, from, to);
    const targetKey = `${itemIdx}:${to + 1}`;
    requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(`[data-slide-key="${targetKey}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  return (
    <section id={`op-song-${itemIdx}`} className="mb-8 scroll-mt-4">
      <div className="group/song mb-2.5 flex items-baseline gap-2 border-b border-border pb-1.5">
        <span className="text-sm font-semibold tabular-nums text-muted-foreground">
          {(itemIdx + 1).toString().padStart(2, "0")}
        </span>
        <h3 className="text-xl font-bold tracking-tight">{title}</h3>
        {author && <span className="text-sm text-muted-foreground">{author}</span>}
        {songKey !== undefined ? (
          <ServiceKeyBadge
            originalKey={chartKey}
            serviceKey={songKey}
            onChange={(key) => onKeyChange(itemIdx, key)}
          />
        ) : (
          chartKey && <KeyBadge songKey={chartKey} className="size-5 self-center text-[10px]" />
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {realSlides.length} slide{realSlides.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => onRemoveSong(itemIdx)}
          title="Remove from service"
          aria-label="Remove from service"
          className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover/song:opacity-100"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={realSlides.map((s) => s.id)} strategy={rectSortingStrategy}>
          <div className="slide-grid">
            {/* Blank "song start" card — navigable, not editable. */}
            <BlankCard
              slideKey={`${itemIdx}:0`}
              active={currentSlideIdx === 0}
              onClick={() => onNavigate(itemIdx, 0)}
            />
            {realSlides.map((slide, overrideIdx) => {
              const navIdx = overrideIdx + 1;
              return (
                <SlideThumb
                  key={slide.id}
                  id={slide.id}
                  slideKey={`${itemIdx}:${navIdx}`}
                  look={look}
                  suggested={suggestedSlideIdx === navIdx ? suggestedTier : null}
                  sectionLabel={showSections ? sectionLabel(slide.section) : ""}
                  sectionType={slide.section.type}
                  lyrics={slide.lyrics}
                  active={currentSlideIdx === navIdx}
                  onNavigate={() => onNavigate(itemIdx, navIdx)}
                  onCommitEdit={(lyrics) => onEditLyrics(itemIdx, overrideIdx, lyrics)}
                  onDuplicate={() => onDuplicate(itemIdx, overrideIdx)}
                  onInsertAfter={() => onInsertSlide(itemIdx, overrideIdx)}
                  openForEditing={openSlideIdx === overrideIdx}
                  onEditingOpened={onEditingOpened}
                  onDelete={() => onDeleteSlide(itemIdx, overrideIdx)}
                />
              );
            })}
            <button
              type="button"
              onClick={() => onAddSlide(itemIdx)}
              className="relative aspect-video w-full overflow-hidden rounded-md border border-dashed border-border text-xs text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
            >
              <span className="absolute inset-0 flex items-center justify-center gap-1.5">
                <Plus className="size-4" />
                Add slide
              </span>
            </button>
          </div>
        </SortableContext>
      </DndContext>
    </section>
  );
});

function BlankCard({
  slideKey,
  active,
  onClick,
}: {
  slideKey: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.currentTarget.blur();
        onClick();
      }}
      data-slide-key={slideKey}
      data-testid="operator-blank-card"
      className={`relative aspect-video w-full scroll-mt-12 scroll-mb-4 overflow-hidden rounded-md border bg-black/95 text-[10px] uppercase tracking-wider text-white/40 transition-[border-color,box-shadow] duration-150 ${
        active
          ? "border-primary ring-2 ring-primary"
          : "border-border hover:border-primary/50 hover:ring-1 hover:ring-primary/50"
      }`}
    >
      <span className="absolute inset-0 flex items-center justify-center">
        Blank · song start
      </span>
    </button>
  );
}

/** AI assist's pulse on a tile: slow and strong for "the band is here",
 * quick and light for "the next slide is due". Still under Reduce motion. */
const SUGGESTED_CLASS = {
  place: "border-yellow-400 animate-assist-pulse motion-reduce:animate-none motion-reduce:ring-2 motion-reduce:ring-yellow-400",
  next: "border-yellow-400/60 animate-assist-pulse-light motion-reduce:animate-none motion-reduce:ring-1 motion-reduce:ring-yellow-400/70",
} as const;

function SlideThumb({
  id,
  slideKey,
  look,
  suggested,
  sectionLabel,
  sectionType,
  lyrics,
  active,
  onNavigate,
  onCommitEdit,
  onDuplicate,
  onInsertAfter,
  openForEditing,
  onEditingOpened,
  onDelete,
}: {
  id: string;
  slideKey: string;
  look: DisplayLook;
  suggested: "next" | "place" | null;
  sectionLabel: string;
  sectionType: SectionType;
  lyrics: string;
  active: boolean;
  onNavigate: () => void;
  onCommitEdit: (lyrics: string) => void;
  onDuplicate: () => void;
  onInsertAfter: () => void;
  openForEditing: boolean;
  onEditingOpened: () => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);

  // Moving the screen off this slide ends its edit. A slide that was never
  // on screen (one just inserted) keeps its edit as the service moves on.
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) setEditing(false);
    wasActive.current = active;
  }, [active]);

  useEffect(() => {
    if (!openForEditing) return;
    setEditing(true);
    onEditingOpened();
  }, [openForEditing, onEditingOpened]);

  // Editing a slide that isn't on screen puts it there first. One that is
  // already showing isn't re-sent: that would count as an operator override.
  const startEdit = useCallback(() => {
    if (!active) onNavigate();
    setEditing(true);
  }, [active, onNavigate]);

  return (
    <SetlistSlideCard
      sortableId={id}
      look={look}
      sectionLabel={sectionLabel}
      sectionType={sectionType}
      lyrics={lyrics}
      emptyText="(blank)"
      selected={active}
      idleClassName={suggested ? SUGGESTED_CLASS[suggested] : undefined}
      editing={editing}
      selectLabel="Go to slide"
      onSelect={onNavigate}
      onStartEdit={startEdit}
      onStopEdit={(next) => {
        setEditing(false);
        if (next !== lyrics) onCommitEdit(next);
      }}
      onInsertAfter={onInsertAfter}
      onDuplicate={onDuplicate}
      onDelete={onDelete}
      data={{
        "data-testid": "operator-slide-card",
        "data-slide-key": slideKey,
        "data-assist": suggested ?? undefined,
      }}
      deleteLabel="Remove"
    />
  );
}


// --- search panel ------------------------------------------------------------

/** The library search, with a Scripture tab beside it when that's on. Both
 * stay mounted so switching back keeps what was typed. */
function RightPanel({ songs, translation }: { songs: React.ReactNode; translation: string }) {
  const [tab, setTab] = useState<"songs" | "scripture">("songs");
  if (!FEATURES.liveScripture) return <>{songs}</>;
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex gap-1 border-b border-border px-3 pt-2">
        {(["songs", "scripture"] as const).map((id) => (
          <button
            key={id}
            type="button"
            data-testid={`operator-tab-${id}`}
            onClick={() => setTab(id)}
            className={cn(
              "-mb-px border-b-2 px-2.5 pb-1.5 text-xs font-medium",
              tab === id
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {id === "songs" ? "Songs" : "Scripture"}
          </button>
        ))}
      </div>
      <div className={cn("min-h-0 flex-1", tab !== "songs" && "hidden")}>{songs}</div>
      <div className={cn("min-h-0 flex-1", tab !== "scripture" && "hidden")}>
        <LiveScripturePanel translation={translation} />
      </div>
    </div>
  );
}

function SearchPanel({
  existingSongIds,
  chartKeys,
  onAddHere,
  onAddToEnd,
}: {
  existingSongIds: Set<string | null>;
  chartKeys: Record<string, string | null>;
  onAddHere: (song: SongRow) => void;
  onAddToEnd: (song: SongRow) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SongRow[]>([]);
  const [suggested, setSuggested] = useState<SongRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    listSongsSorted("popular")
      .then((rows) => {
        if (!cancelled) setSuggested(rows.slice(0, 30));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (query.trim() === "") {
      setResults([]);
      return;
    }
    const timer = setTimeout(() => {
      searchSongs(query)
        .then((rows) => {
          if (!cancelled) setResults(rows);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const visibleSongs =
    query.trim() === ""
      ? suggested.filter((s) => !existingSongIds.has(s.id))
      : results;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-border px-3 py-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search songs or lyrics…"
            className="h-9 pl-9"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {query.trim() === "" && visibleSongs.length === 0 && (
          <div className="px-3 py-3 text-xs text-muted-foreground">
            Type to search your library by title or lyrics.
          </div>
        )}
        {query.trim() !== "" && visibleSongs.length === 0 && (
          <div className="px-3 py-3 text-xs text-muted-foreground">No matches.</div>
        )}
        <ul className="space-y-1">
          {visibleSongs.map((song) => (
            <SearchResult
              key={song.id}
              song={song}
              chartKey={chartKeys[song.id] ?? null}
              alreadyIn={existingSongIds.has(song.id)}
              onAddHere={() => onAddHere(song)}
              onAddToEnd={() => onAddToEnd(song)}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

function SearchResult({
  song,
  chartKey,
  alreadyIn,
  onAddHere,
  onAddToEnd,
}: {
  song: SongRow;
  chartKey: string | null;
  alreadyIn: boolean;
  onAddHere: () => void;
  onAddToEnd: () => void;
}) {
  const preview = firstVerseLines(song.body_text, 2);
  return (
    <li className="group rounded-md px-3 py-2 hover:bg-accent/40">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{song.title}</div>
          {song.author && (
            <div className="truncate text-xs text-muted-foreground">{song.author}</div>
          )}
          {preview && (
            <div className="mt-0.5 truncate text-xs text-muted-foreground/80">{preview}</div>
          )}
        </div>
        {chartKey && <KeyBadge songKey={chartKey} className="size-5 shrink-0 text-[10px]" />}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
        <Button size="sm" variant="secondary" className="h-7 text-xs" onClick={onAddHere}>
          Add here
        </Button>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onAddToEnd}>
          Add to end
        </Button>
        {alreadyIn && (
          <span className="ml-auto text-[10px] text-muted-foreground">in service</span>
        )}
      </div>
    </li>
  );
}
