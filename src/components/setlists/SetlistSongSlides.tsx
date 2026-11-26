/**
 * Per-setlist slide editor. Expanded under a song row in the setlist builder,
 * it shows the song's slides as a responsive 2–4 column grid that the operator
 * can edit, duplicate, reorder, and append to — **scoped to this setlist only**.
 *
 * Edits are persisted as a `slides_override` JSON blob on the setlist item via
 * `setSetlistItemSlides`; the library song is never touched unless the operator
 * explicitly clicks "Save to library". "Reset" clears the override and reverts
 * to the library song's body + default slide order.
 */

import { useEffect, useMemo, useRef, useState } from "react";
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
} from "@dnd-kit/sortable";
import { BookOpen, Palette, Pencil, Plus, RotateCcw, Save, Trash2, Type, Undo2 } from "lucide-react";

import { sectionLabel, type ParsedSection } from "@/lib/parseSong";
import { DisplayLookDialog } from "@/components/presentation/DisplayLookDialog";
import { lookForItem, parseLook, useDisplayLook, useScriptureLook, type DisplayLook } from "@/lib/displayLook";
import {
  parseSlidesOverride,
  resolveSlideSequence,
  serializeSlidesOverride,
  slidesToBody,
  type OverrideSlide,
} from "@/lib/slideSequence";
import {
  itemKind,
  readingBody,
  scriptureContent,
  textContent,
  type ScriptureContent,
} from "@/lib/serviceItems";
import {
  saveReading,
  setSetlistItemContent,
  setSetlistItemLook,
  setSetlistItemSlides,
  updateSong,
  type SetlistItemWithSong,
} from "@/lib/tauri";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { KeyBadge } from "@/components/KeyBadge";
import { ServiceKeyBadge } from "@/components/ServiceKeyBadge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { SetlistSlideCard } from "@/components/setlists/SetlistSlideCard";
import { splitAt } from "@/lib/slideSplit";

const PERSIST_DEBOUNCE_MS = 600;

interface EditableSlide extends OverrideSlide {
  /** Stable local id for React keys + drag identity. */
  uid: string;
}

interface Props {
  item: SetlistItemWithSong;
  number?: number;
  chartKey?: string | null;
  /** The song has a chord chart: a split then keeps to line breaks. */
  hasChords?: boolean;
  /** Change the song's key for this service; without it the key only shows. */
  onKeyChange?: (key: string | null) => void;
  selectedSlideIdx?: number | null;
  onSelectSlide?: (slideIdx: number) => void;
  /** Refresh the parent setlist (after save-to-library / reset / inline edit). */
  onChanged: () => void;
  onRemoveSong?: () => void;
  /** Open the song in the full editor. */
  onEditSong?: (songId: string) => void;
}

let uidCounter = 0;
function nextUid(): string {
  uidCounter += 1;
  return `s${uidCounter}`;
}

/** The library song's slides, as the editable starting point. */
function songSlides(item: SetlistItemWithSong): EditableSlide[] {
  if (!item.song) return [];
  return resolveSlideSequence(item.song.body_text, item.song.slide_order).map((s) => ({
    uid: nextUid(),
    section: s.section,
    lyrics: s.lyrics,
  }));
}

function initialSlides(item: SetlistItemWithSong): EditableSlide[] {
  const override = parseSlidesOverride(item.slides_override);
  if (override) {
    return override.map((s) => ({ uid: nextUid(), section: s.section, lyrics: s.lyrics }));
  }
  return songSlides(item);
}

export function SetlistSongSlides({
  item,
  number,
  chartKey,
  hasChords = false,
  onKeyChange,
  selectedSlideIdx,
  onSelectSlide,
  onChanged,
  onRemoveSong,
  onEditSong,
}: Props) {
  const toast = useToast();
  const confirm = useConfirm();
  const [slides, setSlides] = useState<EditableSlide[]>(() => initialSlides(item));
  const [editingUid, setEditingUid] = useState<string | null>(null);
  const [localSelectedIdx, setLocalSelectedIdx] = useState<number | null>(null);
  const activeSelectedIdx =
    selectedSlideIdx !== undefined ? selectedSlideIdx : localSelectedIdx;

  useEffect(() => {
    if (selectedSlideIdx === null && editingUid !== null) {
      setEditingUid(null);
    }
  }, [selectedSlideIdx, editingUid]);
  // Whether this item currently has setlist-scoped edits. Tracked locally
  // (not from the `item` prop) because edits made in this session persist the
  // override without triggering a parent refresh — the prop would stay stale,
  // leaving Reset wrongly disabled and the banner wrongly reading "Editing".
  const [hasOverride, setHasOverride] = useState(item.slides_override !== null);
  const kind = itemKind(item);
  const scripture = scriptureContent(item);
  // A text item's slides *are* its edits; there is no library copy to differ from.
  const editsShowAsOverride = kind !== "text" && hasOverride;
  const [ownTextOpen, setOwnTextOpen] = useState(false);

  // The source changed underneath (a passage's text filled in or replaced,
  // a library song edited elsewhere): show it, unless this service has its
  // own edits, which win.
  const sourceBody = item.song?.body_text;
  const firstSource = useRef(true);
  useEffect(() => {
    if (firstSource.current) {
      firstSource.current = false;
      return;
    }
    if (!hasOverride) setSlides(songSlides(item));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the source text changes
  }, [sourceBody]);

  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Skip persisting the very first render (the slides we loaded are already
  // what's stored — either the override or, for an untouched item, nothing).
  const dirtyRef = useRef(false);
  // Latest slides, so the unmount-flush closure doesn't capture a stale list.
  const latestRef = useRef(slides);
  const pendingRef = useRef(false);
  latestRef.current = slides;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  function flush() {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    pendingRef.current = false;
    void setSetlistItemSlides(
      item.setlist_id,
      item.id,
      serializeSlidesOverride(latestRef.current),
    )
      .then(() => {
        onChanged();
      })
      .catch((err) => {
        toast.error(`Couldn't save slide edits: ${err instanceof Error ? err.message : err}`);
      });
  }

  // Debounced persist of the current slide list as this item's override.
  useEffect(() => {
    if (!dirtyRef.current) return;
    pendingRef.current = true;
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(flush, PERSIST_DEBOUNCE_MS);
    return () => {
      if (persistTimer.current) clearTimeout(persistTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slides]);

  // Flush a pending save when the editor unmounts (collapse / navigate away).
  useEffect(() => {
    return () => {
      if (pendingRef.current) flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The slides just before the last split, for "Undo split" until the next change.
  const [beforeSplit, setBeforeSplit] = useState<EditableSlide[] | null>(null);

  function mutate(next: EditableSlide[], keepUndo = false) {
    if (!keepUndo) setBeforeSplit(null);
    dirtyRef.current = true;
    setHasOverride(true);
    setSlides(next);
  }

  function selectIndex(idx: number) {
    setLocalSelectedIdx(idx);
    onSelectSlide?.(idx);
    if (editingUid !== null && slides[idx]?.uid !== editingUid) {
      setEditingUid(null);
    }
  }

  function handleEdit(uid: string, lyrics: string) {
    mutate(slides.map((s) => (s.uid === uid ? { ...s, lyrics } : s)));
  }

  function handleDuplicate(uid: string) {
    const idx = slides.findIndex((s) => s.uid === uid);
    if (idx < 0) return;
    const copy: EditableSlide = { ...slides[idx]!, uid: nextUid() };
    mutate([...slides.slice(0, idx + 1), copy, ...slides.slice(idx + 1)]);
    selectIndex(idx + 1);
  }

  /** Cut the slide in two near its middle; the first half stays selected. */
  function handleSplit(uid: string) {
    const idx = slides.findIndex((s) => s.uid === uid);
    if (idx < 0) return;
    const next = splitAt(
      slides,
      idx,
      (slide, lyrics) => ({ uid: nextUid(), section: { ...slide.section }, lyrics }),
      { midLine: !hasChords },
    );
    if (next === slides) return;
    if (editingUid === uid) setEditingUid(null);
    mutate(next, true);
    setBeforeSplit(slides);
    setLocalSelectedIdx(idx);
    onSelectSlide?.(idx);
  }

  /** A blank slide in the same section straight after `uid`, selected and open for typing. */
  function handleInsertAfter(uid: string) {
    const idx = slides.findIndex((s) => s.uid === uid);
    if (idx < 0) return;
    const added: EditableSlide = { uid: nextUid(), section: { ...slides[idx]!.section }, lyrics: "" };
    mutate([...slides.slice(0, idx + 1), added, ...slides.slice(idx + 1)]);
    setLocalSelectedIdx(idx + 1);
    onSelectSlide?.(idx + 1);
    setEditingUid(added.uid);
  }

  function handleDelete(uid: string) {
    const idx = slides.findIndex((s) => s.uid === uid);
    if (editingUid === uid) setEditingUid(null);
    const next = slides.filter((s) => s.uid !== uid);
    mutate(next);
    if (activeSelectedIdx !== null && next.length > 0 && idx >= 0) {
      const clamped = Math.min(activeSelectedIdx, next.length - 1);
      setLocalSelectedIdx(clamped);
      onSelectSlide?.(clamped);
    }
  }

  function handleAdd() {
    // New slide extends the last slide's section (or a generic "other").
    const last = slides[slides.length - 1];
    const section: ParsedSection = last ? last.section : { type: "other", number: 1 };
    const added: EditableSlide = { uid: nextUid(), section, lyrics: "" };
    const nextIdx = slides.length;
    mutate([...slides, added]);
    setLocalSelectedIdx(nextIdx);
    onSelectSlide?.(nextIdx);
    setEditingUid(added.uid);
  }

  // This item's own style for the service, kept here once changed (like the
  // slides, saved without waiting for the setlist to reload).
  const [ownLook, setOwnLook] = useState<string | null>(item.look ?? null);
  const [styling, setStyling] = useState(false);
  const defaultLook = useDisplayLook();
  const scriptureLook = useScriptureLook();
  const look = lookForItem(defaultLook, scriptureLook, { kind, look: ownLook });

  async function saveOwnLook(next: DisplayLook | null) {
    const json = next ? JSON.stringify(next) : null;
    setOwnLook(json);
    try {
      await setSetlistItemLook(item.setlist_id, item.id, json);
      onChanged?.();
    } catch (err) {
      toast.error(`Couldn't save the style: ${err instanceof Error ? err.message : err}`);
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const draggedUid = String(active.id);
    const oldIdx = slides.findIndex((s) => s.uid === active.id);
    const newIdx = slides.findIndex((s) => s.uid === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    mutate(arrayMove(slides, oldIdx, newIdx));
    selectIndex(newIdx);
    requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(`[data-slide-uid="${draggedUid}"]`)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    });
  }

  async function handleSaveToLibrary() {
    if (!item.song) return;
    const ok = await confirm({
      title: "Save to library?",
      message: `This overwrites "${item.song.title}" in your library with these ${slides.length} slide${slides.length === 1 ? "" : "s"}, affecting every setlist that uses it. The setlist override is then cleared.`,
      confirmLabel: "Save to library",
    });
    if (!ok) return;
    // Flush any pending debounce first.
    if (persistTimer.current) clearTimeout(persistTimer.current);
    pendingRef.current = false;
    const overrideSlides: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    try {
      await updateSong(item.song.id, {
        title: item.song.title,
        body_text: slidesToBody(overrideSlides),
        ccli: item.song.ccli,
        author: item.song.author,
        copyright: item.song.copyright,
        default_tempo_wps: item.song.default_tempo_wps,
        // Written literally in order, so no section-order indirection.
        slide_order: null,
      });
      // The library now matches; clear the per-setlist override.
      await setSetlistItemSlides(item.setlist_id, item.id, null);
      dirtyRef.current = false;
      setHasOverride(false);
      toast.success(`Saved "${item.song.title}" to the library.`);
      onChanged();
    } catch (err) {
      toast.error(`Save to library failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleReset() {
    const ok = await confirm({
      title: kind === "scripture" ? "Go back to the passage's text?" : "Use the library version?",
      message:
        kind === "scripture"
          ? "Every edit to this passage in this service goes."
          : "Every edit to this song's slides in this service goes. The library song isn't changed.",
      confirmLabel: kind === "scripture" ? "Go back" : "Use library version",
    });
    if (!ok) return;
    setBeforeSplit(null);
    if (persistTimer.current) clearTimeout(persistTimer.current);
    pendingRef.current = false;
    try {
      await setSetlistItemSlides(item.setlist_id, item.id, null);
      dirtyRef.current = false;
      setHasOverride(false);
      setEditingUid(null);
      setSlides(songSlides(item));
      onChanged();
    } catch (err) {
      toast.error(`Reset failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleSaveReading() {
    const title = textContent(item)?.title ?? "Reading";
    try {
      await saveReading(title, readingBody(slides.map((s) => s.lyrics)));
      toast.success(`Saved "${title}" to the library.`);
    } catch (err) {
      toast.error(`Save to library failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleRename(title: string) {
    const next = title.trim();
    if (!next || next === textContent(item)?.title) return;
    try {
      await setSetlistItemContent(item.setlist_id, item.id, { title: next });
      onChanged();
    } catch (err) {
      toast.error(`Rename failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function handleOwnText(next: ScriptureContent) {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    pendingRef.current = false;
    try {
      await setSetlistItemContent(item.setlist_id, item.id, next);
      // New text replaces any edits made to the old text.
      await setSetlistItemSlides(item.setlist_id, item.id, null);
      dirtyRef.current = false;
      setHasOverride(false);
      setOwnTextOpen(false);
      onChanged();
    } catch (err) {
      toast.error(`Couldn't save the text: ${err instanceof Error ? err.message : err}`);
    }
  }

  const slideIds = useMemo(() => slides.map((s) => s.uid), [slides]);

  return (
    <section
      id={`setlist-song-${item.id}`}
      data-song-block-id={item.id}
      className="mb-8 scroll-mt-4"
    >
      <div className="group/song mb-2.5 flex flex-wrap items-baseline gap-2 border-b border-border pb-1.5">
        {number !== undefined && (
          <span className="text-sm font-semibold tabular-nums text-muted-foreground">
            {number.toString().padStart(2, "0")}
          </span>
        )}
        {kind === "song" && (
          <>
            <h3 className="text-xl font-bold tracking-tight">
              {item.song?.title ?? "Missing song"}
            </h3>
            {item.song?.author && (
              <span className="text-sm text-muted-foreground">{item.song.author}</span>
            )}
          </>
        )}
        {kind === "scripture" && (
          <>
            <BookOpen className="size-4 self-center text-sky-400" aria-hidden />
            <h3 className="text-xl font-bold tracking-tight">{scripture?.reference ?? "Scripture"}</h3>
            {scripture?.abbreviation && !scripture.own_text && (
              <span className="text-sm text-muted-foreground">{scripture.abbreviation}</span>
            )}
            {scripture?.own_text && (
              <span className="text-sm text-muted-foreground">your own text</span>
            )}
          </>
        )}
        {kind === "text" && (
          <>
            <Type className="size-4 self-center text-violet-400" aria-hidden />
            <TitleInput
              key={textContent(item)?.title}
              value={textContent(item)?.title ?? ""}
              onCommit={(t) => void handleRename(t)}
            />
          </>
        )}
        {kind === "song" && onKeyChange ? (
          <ServiceKeyBadge originalKey={chartKey ?? null} serviceKey={item.song_key ?? null} onChange={onKeyChange} />
        ) : (
          chartKey && <KeyBadge songKey={chartKey} className="size-5 self-center text-[10px]" />
        )}

        <div className="ml-auto flex items-center gap-1.5 self-center">
          {beforeSplit && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                mutate(beforeSplit);
                setBeforeSplit(null);
              }}
              title="Put the slide back as it was before the split"
              className="h-7 gap-1 px-2 text-xs"
            >
              <Undo2 className="size-3.5" />
              Undo split
            </Button>
          )}
          {editsShowAsOverride && (
            <>
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
                Edited for this service
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleReset}
                title={
                  kind === "scripture"
                    ? "Discard these edits and go back to the passage's text"
                    : "Discard this service's edits and use the library song"
                }
                className="h-7 gap-1 px-2 text-xs"
              >
                <RotateCcw className="size-3.5" />
                {kind === "scripture" ? "Use passage text" : "Use library version"}
              </Button>
              {kind === "song" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleSaveToLibrary}
                  title="Overwrite the library song with these slides"
                  className="h-7 gap-1 px-2 text-xs"
                >
                  <Save className="size-3.5" />
                  Save to library
                </Button>
              )}
            </>
          )}
          {kind === "scripture" && scripture && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOwnTextOpen((o) => !o)}
              title="Paste the passage from the translation your church reads, with its copyright line"
              className="h-7 gap-1 px-2 text-xs"
            >
              <Pencil className="size-3.5" />
              {scripture.own_text ? "Change text" : "Use my own text"}
            </Button>
          )}
          {kind === "text" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleSaveReading()}
              title="Keep these slides in the library to add to another service"
              className="h-7 gap-1 px-2 text-xs"
            >
              <Save className="size-3.5" />
              Save to library
            </Button>
          )}
          <span className="text-xs text-muted-foreground">
            {slides.length} slide{slides.length === 1 ? "" : "s"}
          </span>
          {kind === "song" && item.song_id && onEditSong && (
            <button
              type="button"
              onClick={() => onEditSong(item.song_id!)}
              title="Edit song"
              aria-label="Edit song"
              className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover/song:opacity-100"
            >
              <Pencil className="size-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={() => setStyling(true)}
            title={ownLook ? "Look for this setlist (its own)" : "Give this a look of its own in this setlist"}
            aria-label="Style"
            data-own-style={ownLook ? "true" : undefined}
            className={`rounded p-1 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover/song:opacity-100 ${
              ownLook ? "text-primary opacity-100" : "text-muted-foreground opacity-0"
            }`}
          >
            <Palette className="size-3.5" />
          </button>
          {onRemoveSong && (
            <button
              type="button"
              onClick={onRemoveSong}
              title="Remove from setlist"
              aria-label="Remove from setlist"
              className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover/song:opacity-100"
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      {styling && (
        <DisplayLookDialog
          onClose={() => setStyling(false)}
          override={{
            title: `Look for “${item.song?.title ?? "this item"}”`,
            description: "Only in this setlist. Everything else keeps its look.",
            value: look,
            onChange: (next) => void saveOwnLook(next),
            onUseDefault: parseLook(ownLook)
              ? () => {
                  void saveOwnLook(null);
                  setStyling(false);
                }
              : undefined,
          }}
        />
      )}

      {ownTextOpen && scripture && (
        <OwnTextEditor
          content={scripture}
          onSave={(next) => void handleOwnText(next)}
          onCancel={() => setOwnTextOpen(false)}
        />
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={slideIds} strategy={rectSortingStrategy}>
          <div className="slide-grid">
            {slides.map((slide, slideIdx) => (
              <SetlistSlideCard
                key={slide.uid}
                sortableId={slide.uid}
                look={look}
                // Songs label slides with their sections; a passage or text doesn't.
                sectionLabel={kind === "song" ? sectionLabel(slide.section) : ""}
                sectionType={slide.section.type}
                lyrics={slide.lyrics}
                emptyText="Empty slide — click to edit"
                selected={activeSelectedIdx === slideIdx}
                editing={editingUid === slide.uid}
                selectLabel="Select slide"
                onSelect={() => selectIndex(slideIdx)}
                onStartEdit={() => {
                  selectIndex(slideIdx);
                  setEditingUid(slide.uid);
                }}
                onStopEdit={() => setEditingUid(null)}
                onChangeLyrics={(lyrics) => handleEdit(slide.uid, lyrics)}
                onInsertAfter={() => handleInsertAfter(slide.uid)}
                onDuplicate={() => handleDuplicate(slide.uid)}
                onSplit={() => handleSplit(slide.uid)}
                splitMidLine={!hasChords}
                onDelete={() => handleDelete(slide.uid)}
                deleteOnKey
                growWhenSelected
                data={{
                  "data-testid": "setlist-slide-card",
                  "data-slide-uid": slide.uid,
                  "data-slide-pos": `${item.id}:${slideIdx}`,
                  "data-selected": activeSelectedIdx === slideIdx ? "true" : undefined,
                }}
              />
            ))}
            <button
              type="button"
              onClick={handleAdd}
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
}

/** A text item's name, edited in place; saved on Enter or leaving the field. */
function TitleInput({ value, onCommit }: { value: string; onCommit: (title: string) => void }) {
  const [draft, setDraft] = useState(value);
  return (
    <Input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onCommit(draft)}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(value);
          e.currentTarget.blur();
        }
      }}
      aria-label="Name"
      // Wide enough for the whole name; `ch` runs narrow for bold text, hence the slack.
      style={{ width: `${Math.max(draft.length, 6) + 3}ch` }}
      className="h-8 min-w-0 max-w-md border-transparent bg-transparent px-1 text-xl font-bold tracking-tight shadow-none hover:border-border focus-visible:border-border"
    />
  );
}

/**
 * "Use my own text": the passage pasted from the translation the church
 * reads (one the app can't bundle), and the copyright line that goes with
 * it. Blank lines in the paste become slide breaks; otherwise it is cut to
 * fit, like any passage.
 */
function OwnTextEditor({
  content,
  onSave,
  onCancel,
}: {
  content: ScriptureContent;
  onSave: (next: ScriptureContent) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(content.own_text ?? "");
  const [credit, setCredit] = useState(content.credit ?? "");
  const hasVerses = (content.verses?.length ?? 0) > 0;
  const { own_text: _ownText, credit: _credit, ...bible } = content;
  return (
    <div className="mb-3 space-y-2 rounded-md border border-border bg-muted/30 p-3">
      <Textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={`Paste ${content.reference} from your church's Bible. A blank line starts a new slide.`}
        className="min-h-28 text-sm"
      />
      <Input
        value={credit}
        onChange={(e) => setCredit(e.target.value)}
        placeholder="Copyright line, e.g. Scripture taken from the Holy Bible, NIV…"
        className="h-8 text-xs"
      />
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className="h-7 text-xs"
          disabled={!text.trim()}
          onClick={() =>
            onSave({ ...bible, own_text: text.trim(), ...(credit.trim() ? { credit: credit.trim() } : {}) })
          }
        >
          Use this text
        </Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onCancel}>
          Cancel
        </Button>
        {content.own_text && hasVerses && (
          <Button size="sm" variant="ghost" className="ml-auto h-7 text-xs" onClick={() => onSave(bible)}>
            Go back to the {content.abbreviation ?? "Bible"} text
          </Button>
        )}
      </div>
    </div>
  );
}
