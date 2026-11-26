import { useCallback, useEffect, useRef, useState } from "react";
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
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ArrowLeft,
  BookOpen,
  Check,
  Music,
  Palette,
  PanelRightClose,
  Plus,
  Radio,
  Search,
  Trash2,
  Type,
  X,
} from "lucide-react";

import {
  addServiceItem,
  addSetlistItem,
  deleteSetlist,
  getSetlist,
  listChartKeys,
  listReadings,
  listSetlists,
  listSongsSorted,
  removeSetlistItem,
  reorderSetlist,
  setSetlistItemKey,
  updateSetlistDate,
  updateSetlistName,
  type SetlistDetail,
  type ReadingRow,
  type SetlistItemWithSong,
  type SongRow,
} from "@/lib/tauri";
import { nameForDate } from "@/lib/setlistName";
import { useServiceDateField } from "@/lib/useServiceDateField";
import {
  itemCountLabel,
  itemKind,
  itemLabel,
  readingSlides,
  scriptureFromPassage,
  textSlides,
} from "@/lib/serviceItems";
import { fillMissingVerses } from "@/lib/scripture/prepared";
import type { ScripturePassage } from "@/types/generated";
import type { StartHow } from "@/components/AppShell";
import { KeyBadge } from "@/components/KeyBadge";
import { useSongFileImport } from "@/components/library/useSongFileImport";
import { ServiceKeyBadge } from "@/components/ServiceKeyBadge";
import { Button } from "@/components/ui/button";
import { DisplayLookDialog } from "@/components/presentation/DisplayLookDialog";
import { BandScreensButton, bandScreensMenuHint } from "@/components/band/BandScreensButton";
import { PeerSyncButton } from "@/components/peerSync/PeerSyncButton";
import { LiveScripturePanel } from "@/components/scripture/LiveScripturePanel";
import { SearchUnderstood } from "@/components/library/SearchUnderstood";
import { withoutPart } from "@/lib/smartSearch/parseQuery";
import { useSmartSearch } from "@/lib/smartSearch/useSmartSearch";
import { useBandServer } from "@/lib/band/useBandHost";
import { FEATURES } from "@/lib/features";
import { useScriptureSettings } from "@/lib/scripture/useScriptureSettings";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { ResizablePanels } from "@/components/ui/ResizablePanels";
import { SetlistSongSlides } from "@/components/setlists/SetlistSongSlides";
import { StartServiceButton } from "@/components/setlists/StartServiceButton";
import { AiReadyPill } from "@/components/setlists/AiReadyPill";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { isTypingTarget, slideAfterArrow } from "@/lib/slideKeys";
import { cn } from "@/lib/utils";

/** How many of the most-used songs the search panel offers before any typing. */
const SUGGESTED_SONGS = 30;

interface Props {
  setlistId: string;
  onClose: () => void;
  onStartService: (setlistId: string, how: StartHow) => void;
  /** Bumped when the library changed elsewhere (e.g. an AI assistant). */
  reloadKey?: number;
  /** Opened straight from "New setlist". Only then is an untouched, empty
   * setlist discarded on the way back — never one that existed before. */
  justCreated?: boolean;
  /** Open a song in the full editor; its back button returns here. */
  onEditSong?: (songId: string) => void;
}

const NAME_AUTOSAVE_MS = 800;
/** Below this window width the songs & scripture panel starts folded away,
 * leaving the slides room for two columns. */
const NARROW_WINDOW_PX = 1100;

function useNarrowWindow(): boolean {
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW_WINDOW_PX);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < NARROW_WINDOW_PX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return narrow;
}

export function SetlistBuilder({
  setlistId,
  onClose,
  onStartService,
  reloadKey = 0,
  justCreated = false,
  onEditSong,
}: Props) {
  const toast = useToast();
  const confirm = useConfirm();
  // So the band and the leader can get their devices connected before the
  // service — essential when nobody will be at the operator view to help.
  const band = useBandServer();
  const scriptureSettings = useScriptureSettings();

  const [detail, setDetail] = useState<SetlistDetail | null>(null);
  const [name, setName] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [selectedSlide, setSelectedSlide] = useState<{
    itemId: string;
    slideIdx: number;
  } | null>(null);
  const [rightTab, setRightTab] = useState<"songs" | "scripture">("songs");
  const narrow = useNarrowWindow();
  // Folds with the window; the rail's buttons open it again.
  const [searchOpen, setSearchOpen] = useState(!narrow);
  useEffect(() => setSearchOpen(!narrow), [narrow]);
  // Before anything is typed, the search panel offers the songs used most.
  const [suggested, setSuggested] = useState<SongRow[]>([]);
  // Readings saved to the library, searched alongside the songs.
  const [readings, setReadings] = useState<ReadingRow[]>([]);
  const [chartKeys, setChartKeys] = useState<Record<string, string | null>>({});
  const [lookOpen, setLookOpen] = useState(false);
  const [bandSignal, setBandSignal] = useState(0);
  const [peerSignal, setPeerSignal] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);
  const programmaticScrollUntilRef = useRef<number>(0);

  const selectSlide = useCallback((itemId: string, slideIdx: number) => {
    setSelectedSlide({ itemId, slideIdx });
    setActiveItemId(itemId);
    programmaticScrollUntilRef.current = Date.now() + 500;
    requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(`[data-rail-item-id="${itemId}"]`)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
      gridRef.current
        ?.querySelector<HTMLElement>(`[data-slide-pos="${itemId}:${slideIdx}"]`)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    });
  }, []);

  const scrollToSong = useCallback((itemId: string) => {
    setActiveItemId(itemId);
    setSelectedSlide({ itemId, slideIdx: 0 });
    programmaticScrollUntilRef.current = Date.now() + 700;
    requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(`[data-rail-item-id="${itemId}"]`)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
      const el = document.getElementById(`setlist-song-${itemId}`);
      const container = gridRef.current;
      if (el && container) {
        container.scrollTo({
          top: Math.max(0, el.offsetTop - 16),
          behavior: "smooth",
        });
      } else {
        el?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      }
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    listSongsSorted("popular")
      .then((rows) => {
        if (!cancelled) setSuggested(rows.slice(0, SUGGESTED_SONGS));
      })
      .catch(() => undefined);
    listChartKeys()
      .then((keys) => {
        if (!cancelled) setChartKeys(keys);
      })
      .catch(() => undefined);
    listReadings()
      .then((rows) => {
        if (!cancelled) setReadings(rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const nameSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nameDirty = useRef(false);
  // Unlike `nameDirty`, never reset by the autosave: a name typed on this
  // visit means the setlist is wanted, even with no songs in it yet.
  const renamed = useRef(false);
  /** The name as last saved, for putting back an emptied box. */
  const savedName = useRef("");
  const everHadSongs = useRef(false);
  if (detail && detail.items.length > 0) everHadSongs.current = true;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  // Initial load
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    getSetlist(setlistId)
      .then((d) => {
        if (cancelled) return;
        if (!d) {
          setError("This service was deleted.");
          return;
        }
        setDetail(d);
        setName(d.name);
        savedName.current = d.name;
        if (d.items.length > 0) {
          setActiveItemId(d.items[0]!.id);
          setSelectedSlide({ itemId: d.items[0]!.id, slideIdx: 0 });
        }
        // A passage added by an assistant has only its reference; the
        // Bible here fills in the text.
        void fillMissingVerses(d)
          .then((filled) => (filled && !cancelled ? refresh() : undefined))
          .catch(() => undefined);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [setlistId]);

  useEffect(() => {
    if (!detail || detail.items.length === 0) return;
    if (!selectedSlide || !detail.items.some((i) => i.id === selectedSlide.itemId)) {
      const firstId = detail.items[0]!.id;
      setActiveItemId(firstId);
      setSelectedSlide({ itemId: firstId, slideIdx: 0 });
    }
  }, [detail, selectedSlide]);

  // Arrow-key slide navigation across songs in the setlist.
  useEffect(() => {
    if (!detail || detail.items.length === 0) return;
    function onKeyDown(ev: KeyboardEvent) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey) return;
      if (!ev.key.startsWith("Arrow") || isTypingTarget(ev.target)) return;
      const items = detail!.items;
      const songs = items.map((item) => ({
        id: item.id,
        slideCount:
          gridRef.current?.querySelectorAll(
            `[data-song-block-id="${item.id}"] [data-testid="setlist-slide-card"]`,
          ).length ?? 0,
      }));
      const cur = selectedSlide ?? { itemId: activeItemId ?? items[0]!.id, slideIdx: 0 };
      ev.preventDefault();
      const next = slideAfterArrow(ev.key, songs, cur);
      if (next) selectSlide(next.itemId, next.slideIdx);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detail, selectedSlide, activeItemId, selectSlide]);
  // The initial load above covers mount, so skip the first run.
  const seenReloadKey = useRef(reloadKey);
  useEffect(() => {
    if (seenReloadKey.current === reloadKey) return;
    seenReloadKey.current = reloadKey;
    void refresh().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // Search results: titles, lyrics, topics and filters ("easter songs we
  // haven't played lately"), the same search as the library's.
  const { result: found } = useSmartSearch(searchQuery, reloadKey);

  // Name autosave
  useEffect(() => {
    if (!nameDirty.current) return;
    if (nameSaveTimer.current) clearTimeout(nameSaveTimer.current);
    nameSaveTimer.current = setTimeout(() => {
      void persistName();
    }, NAME_AUTOSAVE_MS);
    return () => {
      if (nameSaveTimer.current) clearTimeout(nameSaveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  /** A new date; a name still made from the old date follows it. */
  const nameNow = useRef(name);
  nameNow.current = name;
  async function changeDate(date: string) {
    // Picking a date means this service is wanted, even with nothing in it yet.
    renamed.current = true;
    try {
      await updateSetlistDate(setlistId, date);
      setDetail((d) => (d ? { ...d, service_date: date } : d));
      const current = nameNow.current;
      const others = (await listSetlists()).filter((s) => s.id !== setlistId).map((s) => s.name);
      const next = nameForDate(current.trim(), date, others);
      if (next !== current) {
        nameNow.current = next;
        setName(next);
        await updateSetlistName(setlistId, next);
        savedName.current = next;
      }
    } catch (err) {
      toast.error(`Couldn't change the date: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }
  const dateField = useServiceDateField(detail?.service_date, changeDate);

  async function persistName() {
    if (!detail || !nameDirty.current) return;
    try {
      // An emptied name isn't saved; leaving the box puts the last one back.
      if (!name.trim()) return;
      await updateSetlistName(setlistId, name.trim());
      savedName.current = name.trim();
      nameDirty.current = false;
    } catch (err) {
      toast.error(`Rename failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // Leaving another way (the sidebar, say) gets the same tidy-up as the back
  // button: an untouched new setlist goes, and a half-typed name is kept.
  const leftByButton = useRef(false);
  const onLeave = useRef<() => void>(() => undefined);
  onLeave.current = () => {
    if (leftByButton.current) return;
    if (shouldDiscardOnBack({ justCreated, renamed: renamed.current, hadSongs: everHadSongs.current })) {
      void deleteSetlist(setlistId).catch(() => undefined);
    } else if (nameDirty.current) {
      void persistName();
    }
  };
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // StrictMode unmounts and remounts at once in development; only a
      // builder still gone a moment later has really been left.
      setTimeout(() => {
        if (!mounted.current) onLeave.current();
      }, 0);
    };
  }, []);

  async function refresh() {
    const d = await getSetlist(setlistId);
    if (d) setDetail(d);
  }

  async function handleAdd(song: SongRow) {
    try {
      await addSetlistItem(setlistId, song.id);
      setSearchQuery("");
      await refresh();
    } catch (err) {
      toast.error(`Add failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // Song files dropped on the service: into the library, then onto the end
  // of this service — unless the song is already in it.
  const { dropProps, ui: importUi } = useSongFileImport({
    serviceName: name,
    dropHint: "Drop song files to add them to this service",
    onImported: (message, songIds) => void addImported(message, songIds),
  });

  async function addImported(message: string, songIds: string[]) {
    try {
      const fresh = await getSetlist(setlistId);
      const inService = new Set((fresh ?? detail)?.items.map((i) => i.song_id) ?? []);
      const toAdd = songIds.filter((id) => !inService.has(id));
      for (const id of toAdd) await addSetlistItem(setlistId, id);
      await refresh();
      listChartKeys().then(setChartKeys).catch(() => undefined);
      const already = songIds.length - toAdd.length;
      toast.success(
        [
          message !== "Imported nothing." && message,
          toAdd.length > 0 && `Added ${toAdd.length} to this service.`,
          already > 0 && `${already} already in it.`,
        ]
          .filter(Boolean)
          .join(" "),
      );
    } catch (err) {
      toast.error(`Add failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  /** Passages and text land after the selected item, where the operator is
   * working, rather than at the end; several in a row stay in order. */
  async function addAfterSelection(
    adds: { kind: "scripture" | "text"; content: object; slidesOverride?: string }[],
  ) {
    let after = selectedSlide?.itemId ?? activeItemId ?? detail?.items[detail.items.length - 1]?.id ?? null;
    let first: string | null = null;
    try {
      for (const a of adds) {
        const id = await addServiceItem(setlistId, a.kind, a.content, {
          slidesOverride: a.slidesOverride ?? null,
          afterItemId: after,
        });
        first ??= id;
        after = id;
      }
      setSearchQuery("");
      await refresh();
      if (first) scrollToSong(first);
    } catch (err) {
      toast.error(`Add failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  function handleAddPassages(passages: ScripturePassage[]) {
    return addAfterSelection(passages.map((p) => ({ kind: "scripture", content: scriptureFromPassage(p) })));
  }

  function handleNewText(title: string) {
    const t = title.trim() || "Text";
    return addAfterSelection([{ kind: "text", content: { title: t }, slidesOverride: textSlides([t]) }]);
  }

  function handleAddReading(r: ReadingRow) {
    return addAfterSelection([
      { kind: "text", content: { title: r.title }, slidesOverride: readingSlides(r.body_text) },
    ]);
  }

  /** Play a song in another key for this service (null: its own key). */
  async function handleKeyChange(item: SetlistItemWithSong, key: string | null) {
    setDetail((d) => d && { ...d, items: d.items.map((it) => (it.id === item.id ? { ...it, song_key: key } : it)) });
    try {
      await setSetlistItemKey(setlistId, item.id, key);
    } catch (err) {
      toast.error(`Couldn't change the key: ${err instanceof Error ? err.message : err}`);
      await refresh();
    }
  }

  async function handleRemove(itemId: string) {
    try {
      await removeSetlistItem(setlistId, itemId);
      await refresh();
    } catch (err) {
      toast.error(`Remove failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleDelete() {
    const ok = await confirm({
      title: `Delete "${name || "this setlist"}"?`,
      message: "This can't be undone. (The songs themselves stay in your library.)",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteSetlist(setlistId);
      onClose();
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleDragEnd(event: DragEndEvent) {
    if (!detail) return;
    const { active, over } = event;
    const draggedId = String(active.id);
    if (!over || active.id === over.id) {
      scrollToSong(draggedId);
      return;
    }
    const oldIdx = detail.items.findIndex((i) => i.id === active.id);
    const newIdx = detail.items.findIndex((i) => i.id === over.id);
    if (oldIdx < 0 || newIdx < 0) return;

    // Optimistic update + follow the dragged song in the center slide grid
    const reordered = arrayMove(detail.items, oldIdx, newIdx);
    setDetail({ ...detail, items: reordered });
    scrollToSong(draggedId);

    try {
      await reorderSetlist(
        setlistId,
        reordered.map((i) => i.id),
      );
    } catch (err) {
      toast.error(`Reorder failed: ${err instanceof Error ? err.message : err}`);
      // Revert on failure
      void refresh();
    }
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        <span>{error}</span>
        <Button variant="outline" size="sm" onClick={onClose}>
          Back
        </Button>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading setlist…
      </div>
    );
  }

  async function handleEditSong(songId: string) {
    // Leaving unmounts the builder, which would drop a pending rename.
    leftByButton.current = true;
    if (nameDirty.current) await persistName();
    onEditSong?.(songId);
  }

  async function handleBack() {
    leftByButton.current = true;
    // "New setlist" then straight back out leaves nothing worth keeping, so
    // Home doesn't fill with empty drafts.
    if (shouldDiscardOnBack({ justCreated, renamed: renamed.current, hadSongs: everHadSongs.current })) {
      await deleteSetlist(setlistId).catch(() => undefined);
    } else if (nameDirty.current) {
      await persistName();
    }
    onClose();
  }

  const itemSongIds = new Set(detail.items.map((i) => i.song_id));
  const visibleSongs =
    searchQuery.trim() === ""
      ? suggested // added songs stay put (ticked), so the next click lands where aimed
      : (found?.songs ?? []);
  const q = searchQuery.trim().toLowerCase();
  const visibleReadings = readings.filter(
    (r) => q === "" || r.title.toLowerCase().includes(q) || r.body_text.toLowerCase().includes(q),
  );

  // An empty setlist needs the search to get started, so it stays open.
  const searchShown = searchOpen || detail.items.length === 0;
  function openSearch(tab: "songs" | "scripture") {
    setRightTab(tab);
    setSearchOpen(true);
  }

  return (
    <div
      data-testid="setlist-workspace"
      className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground"
      {...dropProps}
    >
      {importUi}
      <header className="flex items-center justify-between gap-3 border-b border-border bg-background px-4 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void handleBack()}
            title="Back to Home"
            aria-label="Back"
            className="h-9 shrink-0 gap-1.5 px-2.5 text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
            <span className="text-xs font-medium">Home</span>
          </Button>
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              nameDirty.current = true;
              renamed.current = true;
            }}
            onBlur={() => {
              if (!name.trim()) {
                setName(savedName.current);
                nameDirty.current = false;
                return;
              }
              void persistName();
            }}
            placeholder="Untitled service"
            className="h-9 max-w-md text-base font-semibold"
          />
          <Input
            type="date"
            {...dateField}
            aria-label="Service date"
            title="The day this service is for"
            className="h-9 w-auto shrink-0 text-sm"
          />
          <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
            {itemCountLabel(detail.items)}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          <PeerSyncButton
            setlistId={setlistId}
            setlistName={name}
            onMessage={(m, kind) => (kind === "error" ? toast.error(m) : toast.success(m))}
            openSignal={peerSignal}
            hideWhenAlone
            align="right"
          />
          <AiReadyPill />
          <StartServiceButton
            onStart={(how) => onStartService(setlistId, how)}
            disabled={detail.items.length === 0}
          />
          {/* No button: "⋯" opens the panel, which drops from here. */}
          <BandScreensButton
            band={band}
            align="right"
            hideButton
            openSignal={bandSignal}
            note="Devices can connect now — they'll show the songs as soon as the service starts."
          />
          <Menu label="More options">
            <MenuItem
              icon={<Music />}
              onSelect={() => setBandSignal((n) => n + 1)}
              hint={bandScreensMenuHint(band)}
            >
              Band screens…
            </MenuItem>
            <MenuItem icon={<Palette />} onSelect={() => setLookOpen(true)}>
              Display look…
            </MenuItem>
            <MenuItem icon={<Radio />} onSelect={() => setPeerSignal((n) => n + 1)}>
              Send or get a setlist…
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} onSelect={() => void handleDelete()} destructive>
              Delete setlist
            </MenuItem>
          </Menu>
        </div>
        {lookOpen && <DisplayLookDialog onClose={() => setLookOpen(false)} />}
      </header>

      <ResizablePanels
        className="flex-1"
        flexIndex={1}
        initialSizes={[240, 0, 340]}
        minSizes={[180, 320, 260]}
        storageKey="setlist_workspace_panels_v1"
        collapsed={[false, false, !searchShown]}
      >
        {/* Left rail: setlist song order (drag-to-reorder + click-to-scroll) */}
        <div className="flex h-full min-h-0 flex-col bg-muted/20">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Setlist
            </span>
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {detail.items.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {detail.items.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                No songs yet.
              </div>
            ) : (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={detail.items.map((i) => i.id)}
                  strategy={verticalListSortingStrategy}
                >
                  <ul className="space-y-0.5 px-1">
                    {detail.items.map((it, idx) => (
                      <SortableRailItem
                        key={it.id}
                        item={it}
                        number={idx + 1}
                        chartKey={it.song_id ? (chartKeys[it.song_id] ?? null) : null}
                        onKeyChange={(key) => void handleKeyChange(it, key)}
                        active={activeItemId === it.id}
                        onSelect={() => scrollToSong(it.id)}
                        onRemove={() => handleRemove(it.id)}
                      />
                    ))}
                  </ul>
                </SortableContext>
              </DndContext>
            )}
          </div>
        </div>

        {/* Center: continuous slide grid across all songs in the setlist */}
        <div
          ref={gridRef}
          onScroll={(e) => {
            if (Date.now() < programmaticScrollUntilRef.current) return;
            const container = e.currentTarget;
            const blocks = container.querySelectorAll<HTMLElement>("[data-song-block-id]");
            const threshold = container.scrollTop + 80;
            let currentId: string | null = null;
            for (const block of blocks) {
              if (block.offsetTop <= threshold) {
                currentId = block.dataset.songBlockId ?? null;
              } else {
                break;
              }
            }
            if (!currentId && blocks.length > 0) {
              currentId = blocks[0]!.dataset.songBlockId ?? null;
            }
            if (currentId && currentId !== activeItemId) {
              setActiveItemId(currentId);
            }
          }}
          className="relative h-full overflow-y-auto px-6 py-4"
        >
          {detail.items.length === 0 ? (
            <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">
              Search for songs on the right and click to add them to this setlist.
              Bible passages and text slides are added from the right too.
            </div>
          ) : (
            detail.items.map((it, idx) => (
              <SetlistSongSlides
                key={it.id}
                item={it}
                number={idx + 1}
                chartKey={it.song_id ? (chartKeys[it.song_id] ?? null) : null}
                onKeyChange={(key) => void handleKeyChange(it, key)}
                selectedSlideIdx={
                  selectedSlide?.itemId === it.id ? selectedSlide.slideIdx : null
                }
                onSelectSlide={(slideIdx) => selectSlide(it.id, slideIdx)}
                onChanged={refresh}
                onRemoveSong={() => handleRemove(it.id)}
                onEditSong={onEditSong ? (songId) => void handleEditSong(songId) : undefined}
              />
            ))
          )}
        </div>

        {/* Right: Songs & Scripture tabs, or a thin rail when folded away */}
        {!searchShown ? (
          <div className="flex h-full flex-col items-center gap-1 border-l border-border px-1.5 py-2">
            <button
              type="button"
              data-testid="open-search-songs"
              onClick={() => openSearch("songs")}
              title="Add songs"
              aria-label="Add songs"
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
            >
              <Search className="size-4" />
            </button>
            {FEATURES.liveScripture && (
              <button
                type="button"
                onClick={() => openSearch("scripture")}
                title="Add scripture"
                aria-label="Add scripture"
                className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
              >
                <BookOpen className="size-4" />
              </button>
            )}
          </div>
        ) : (
        <div className="flex h-full flex-col overflow-hidden">
          {(FEATURES.liveScripture || narrow) && (
            <div className="flex items-center gap-1 border-b border-border px-3 pt-2">
              {(FEATURES.liveScripture ? (["songs", "scripture"] as const) : []).map((id) => (
                <button
                  key={id}
                  type="button"
                  data-testid={`setlist-tab-${id}`}
                  onClick={() => setRightTab(id)}
                  className={cn(
                    "-mb-px border-b-2 px-2.5 pb-1.5 text-xs font-medium",
                    rightTab === id
                      ? "border-foreground text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                  )}
                >
                  {id === "songs" ? "Songs" : "Scripture"}
                </button>
              ))}
              {narrow && detail.items.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSearchOpen(false)}
                  title="Hide"
                  aria-label="Hide song search"
                  className="-mt-1.5 ml-auto rounded-md p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                >
                  <PanelRightClose className="size-4" />
                </button>
              )}
            </div>
          )}

          <div className={cn("flex min-h-0 flex-1 flex-col overflow-hidden", rightTab !== "songs" && "hidden")}>
            <div className="border-b border-border px-3 py-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search songs, lyrics or topics…"
                  className="h-9 pl-9"
                />
              </div>
            </div>
            {searchQuery.trim() !== "" && found && (
              <SearchUnderstood
                result={found}
                onRemove={(part) => setSearchQuery(withoutPart(searchQuery, part))}
                className="px-3"
              />
            )}
            <div className="flex-1 overflow-y-auto px-2 py-2">
              {searchQuery.trim() === "" && visibleSongs.length === 0 && (
                <div className="px-3 py-3 text-xs text-muted-foreground">
                  Type to search your library by title, lyrics or topic.
                </div>
              )}
              {searchQuery.trim() !== "" && visibleSongs.length === 0 && visibleReadings.length === 0 && (
                <div className="px-3 py-3 text-xs text-muted-foreground">
                  No songs match.
                </div>
              )}
              <ul className="space-y-0.5">
                {visibleSongs.map((song) => {
                  const alreadyIn = itemSongIds.has(song.id);
                  const songKey = chartKeys[song.id] ?? null;
                  return (
                    <li key={song.id}>
                      <button
                        type="button"
                        onClick={() => handleAdd(song)}
                        className="group flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors hover:bg-accent/40"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{song.title}</div>
                          <div
                            className={cn(
                              "truncate text-xs text-muted-foreground",
                              found?.reasons[song.id] && searchQuery.trim() !== "" && "italic",
                            )}
                          >
                            {(searchQuery.trim() !== "" && found?.reasons[song.id]) || firstLine(song.body_text)}
                          </div>
                        </div>
                        {songKey && (
                          <KeyBadge songKey={songKey} className="size-5 text-[10px]" />
                        )}
                        {alreadyIn ? (
                          <span title="In the setlist — click to add it again (a reprise)">
                            <Check aria-label="In the setlist" className="size-4 shrink-0 text-primary" />
                          </span>
                        ) : (
                          <Plus className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {visibleReadings.length > 0 && (
                <>
                  <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Readings
                  </div>
                  <ul className="space-y-0.5" data-testid="reading-results">
                    {visibleReadings.map((r) => (
                      <li key={r.id}>
                        <button
                          type="button"
                          onClick={() => void handleAddReading(r)}
                          className="group flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors hover:bg-accent/40"
                        >
                          <Type className="size-4 shrink-0 text-violet-400" aria-hidden />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">{r.title}</div>
                            <div className="truncate text-xs text-muted-foreground">{firstLine(r.body_text)}</div>
                          </div>
                          <Plus className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {searchQuery.trim() !== "" && (
                <button
                  type="button"
                  data-testid="new-text-slide"
                  onClick={() => void handleNewText(searchQuery)}
                  className="group mt-1 flex w-full items-center gap-3 rounded-md border border-dashed border-border px-3 py-2 text-left text-sm transition-colors hover:border-foreground/40 hover:bg-accent/40"
                >
                  <Type className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">
                    New text slide <span className="font-medium">“{searchQuery.trim()}”</span>
                  </span>
                  <Plus className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                </button>
              )}
            </div>
          </div>

          {FEATURES.liveScripture && (
            <div className={cn("min-h-0 flex-1", rightTab !== "scripture" && "hidden")}>
              <LiveScripturePanel
                translation={scriptureSettings.translation}
                onAdd={handleAddPassages}
              />
            </div>
          )}
        </div>
        )}
      </ResizablePanels>
    </div>
  );
}

function SortableRailItem({
  item,
  number,
  chartKey,
  onKeyChange,
  active,
  onSelect,
  onRemove,
}: {
  item: SetlistItemWithSong;
  number: number;
  chartKey: string | null;
  onKeyChange: (key: string | null) => void;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  // The whole row is the drag handle; a click still selects, since a drag
  // needs a few pixels of movement first.
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.id,
  });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  const kind = itemKind(item);
  // A text item's slides are its own; only a song or passage can differ from its source.
  const edited = kind !== "text" && item.slides_override !== null;
  const label = itemLabel(item);

  return (
    <li ref={setNodeRef} style={style} data-rail-item-id={item.id}>
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
          onClick={onSelect}
          disabled={!item.song}
          title={label}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <span className="w-4 shrink-0 tabular-nums text-[11px] opacity-60">
            {number.toString().padStart(2, "0")}
          </span>
          {kind === "scripture" && <BookOpen className="size-3.5 shrink-0 text-sky-400" aria-label="Bible passage" />}
          {kind === "text" && <Type className="size-3.5 shrink-0 text-violet-400" aria-label="Text" />}
          <span className="truncate">{label}</span>
          {edited && (
            <span className="shrink-0 rounded bg-amber-500/15 px-1 py-0.5 text-[9px] font-normal text-amber-400">
              edited
            </span>
          )}
        </button>
        {kind === "song" && (
          <ServiceKeyBadge originalKey={chartKey} serviceKey={item.song_key ?? null} onChange={onKeyChange} />
        )}
        <button
          type="button"
          onClick={onRemove}
          title="Remove from setlist"
          aria-label="Remove from setlist"
          className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
        >
          <X className="size-3.5" />
        </button>
      </div>
    </li>
  );
}

function firstLine(body: string): string {
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("[") && trimmed.endsWith("]")) continue;
    return trimmed;
  }
  return "";
}

/** Whether leaving the setlist should delete it: only a brand-new one that
 * was never named and never had a song, even one removed again. */
export function shouldDiscardOnBack(state: {
  justCreated: boolean;
  renamed: boolean;
  hadSongs: boolean;
}): boolean {
  return state.justCreated && !state.renamed && !state.hadSongs;
}
