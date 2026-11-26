import { useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy } from "@dnd-kit/sortable";
import { Check, Pencil, Plus, Scissors, Sparkles, Trash2 } from "lucide-react";

import {
  parseSong,
  sectionLabel,
  sectionLabelClass,
  type ParsedSection,
} from "@/lib/parseSong";
import { resolveSlideSequence, slidesToBody, type OverrideSlide } from "@/lib/slideSequence";
import { alignSlides, type ChordChart, type PlacedChord } from "@/lib/chords/chart";
import {
  chordToNashville,
  respellChord,
  splitWordIntoSyllables,
  type WordSyllable,
} from "@/lib/chords/chord";
import { diatonicChordItemsForKey, resolveChordInput } from "@/lib/chords/editorText";
import type { SongSuggestion } from "@/lib/chords/suggestions";
import { ChordLyrics } from "@/components/band/ChordLyrics";
import { useDisplayLook } from "@/lib/displayLook";
import { Textarea } from "@/components/ui/textarea";
import { ChordPickerPopover } from "@/components/library/ChordPickerPopover";
import { AiChangeGlow, ProjectorSlideCard } from "@/components/library/ProjectorSlideCard";
import { splitAt } from "@/lib/slideSplit";

export type SetWordChordHandler = (
  slideLyrics: string,
  lineIdx: number,
  wordStart: number,
  wordEnd: number,
  chordPos: number,
  nextChord: string | null,
) => void;

export type MoveChordHandler = (
  slideLyrics: string,
  fromLineIdx: number,
  fromPos: number,
  toLineIdx: number,
  toPos: number,
  chordSymbol: string,
) => void;

export type SetInstrumentalHandler = (
  slotIdx: number,
  runIdxInSlot: number,
  label: string,
  chords: string[],
) => void;

export type ChordDisplayMode = "letters" | "numbers";

interface Props {
  body: string;
  /** Optional default slide order; repeated sections show repeated slides. */
  slideOrder?: string | null;
  /** When set, draw its chords above the lyrics (the band's view). */
  chart?: ChordChart | null;
  /** The song has chords, shown or not: a split then keeps to line breaks. */
  hasChords?: boolean;
  /** Called just before a slide is split, so the editor can offer Undo. */
  onBeforeSplit?: () => void;
  /** Key used for the quick diatonic chord palette and Nashville numbers. */
  chartKey?: string | null;
  /** Whether chords should be spelled with flats (true) or sharps (false). */
  preferFlats?: boolean;
  /** Display chords as standard letters (G, C, D) or Nashville numbers (1, 4, 5). */
  displayMode?: ChordDisplayMode;
  /** When provided, words/syllables in the preview can be clicked to add/change/remove chords. */
  onSetWordChord?: SetWordChordHandler;
  /** When provided, chords in the preview can be dragged to another word or syllable. */
  onMoveChord?: MoveChordHandler;
  /** When provided, instrumental sections (Intro, Turnaround, Outro) can be added/edited. */
  onSetInstrumental?: SetInstrumentalHandler;
  /** When provided, slides can be edited, added, duplicated, reordered, or deleted directly. */
  onChangeBody?: (nextBody: string) => void;
  /** Optional ghost suggestions from the AI / Cursor-Tab next-edit engine. */
  suggestions?: SongSuggestion[];
  /** Currently focused suggestion ID (receives primary Tab highlight). */
  activeSuggestionId?: string | null;
  /** Accept a single ghost suggestion (Tab, or a click when dismiss is not wired). */
  onAcceptSuggestion?: (suggestion: SongSuggestion) => void;
  /** Clicking a ghost or its word retires that prediction and opens the chord picker. */
  onDismissSuggestion?: (suggestion: SongSuggestion) => void;
  /** Accept all suggestions on a specific slide card at once. */
  onAcceptSlideSuggestions?: (slideIdx: number) => void;
  /** Slides an AI take just changed. `token` restarts the glow. */
  aiPulse?: { token: number; slides: number[] } | null;
}

interface WordSegment {
  segStart: number;
  segEnd: number;
  text: string;
  chord: string | null;
  chordPos: number;
}

interface WordToken {
  word: string;
  trailingSpace: string;
  start: number;
  end: number;
  primaryChordPos: number;
  primaryChord: string | null;
  segments: WordSegment[];
  syllables: WordSyllable[];
}

function tokenizeLineWithSegments(text: string, chords: PlacedChord[]): {
  tokens: WordToken[];
  trailingChords: PlacedChord[];
} {
  const sorted = [...chords].sort((a, b) => a.pos - b.pos);
  const tokens: WordToken[] = [];
  const re = /(\S+)(\s*)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const start = m.index;
    const word = m[1]!;
    const trailingSpace = m[2]!;
    const end = start + m[0].length;
    const inWord = sorted.filter((c) => c.pos >= start && c.pos < end);
    const syllables = splitWordIntoSyllables(word).map((s) => ({
      text: s.text,
      start: start + s.start,
      end: start + s.end,
    }));

    const segments: WordSegment[] = [];
    if (inWord.length === 0) {
      segments.push({
        segStart: start,
        segEnd: end,
        text: m[0],
        chord: null,
        chordPos: start,
      });
    } else {
      let cursor = start;
      let idx = 0;
      while (idx < inWord.length) {
        const pos = Math.max(start, Math.min(inWord[idx]!.pos, end - 1));
        if (pos > cursor) {
          segments.push({
            segStart: cursor,
            segEnd: pos,
            text: text.slice(cursor, pos),
            chord: null,
            chordPos: cursor,
          });
        }
        const sameSpot: string[] = [];
        while (idx < inWord.length && Math.max(start, Math.min(inWord[idx]!.pos, end - 1)) === pos) {
          sameSpot.push(inWord[idx++]!.chord);
        }
        const nextPos =
          idx < inWord.length
            ? Math.max(start, Math.min(inWord[idx]!.pos, end - 1))
            : end;
        segments.push({
          segStart: pos,
          segEnd: nextPos,
          text: text.slice(pos, nextPos),
          chord: sameSpot.join(" "),
          chordPos: pos,
        });
        cursor = nextPos;
      }
      if (cursor < end) {
        segments.push({
          segStart: cursor,
          segEnd: end,
          text: text.slice(cursor, end),
          chord: null,
          chordPos: cursor,
        });
      }
    }

    tokens.push({
      word,
      trailingSpace,
      start,
      end,
      primaryChordPos: inWord[0]?.pos ?? start,
      primaryChord: inWord.length > 0 ? inWord.map((c) => c.chord).join(" ") : null,
      segments,
      syllables,
    });
  }
  const trailingChords = sorted.filter((c) => c.pos >= text.length);
  return { tokens, trailingChords };
}

interface DragState {
  slideIdx: number;
  slideLyrics: string;
  fromLineIdx: number;
  fromPos: number;
  chord: string;
  toLineIdx: number;
  toPos: number;
  startX: number;
  startY: number;
  moved: boolean;
}

/** What a new slide holds in the song text until its words are typed. */
const NEW_SLIDE_STAND_IN = "New slide lyrics…";

const INSTRUMENTAL_LABELS = ["Intro", "Turnaround", "Interlude", "Instrumental", "Outro"];

export function SongPreview({
  body,
  slideOrder,
  chart,
  hasChords = false,
  onBeforeSplit,
  chartKey,
  preferFlats,
  displayMode = "letters",
  onSetWordChord,
  onMoveChord,
  onSetInstrumental,
  onChangeBody,
  suggestions = [],
  activeSuggestionId = null,
  onAcceptSuggestion,
  onDismissSuggestion,
  aiPulse = null,
}: Props) {
  // When editing in the library (`onChangeBody` provided), we author the canonical
  // slides from `parseSong(body).slides` so repeated sections in `slideOrder` stay DRY.
  // When used as a pure preview (no `onChangeBody`), we resolve `slideOrder` as before.
  const parsedSong = useMemo(() => parseSong(body), [body]);
  const slides = useMemo(
    () =>
      onChangeBody
        ? parsedSong.slides.map((s, idx) => ({
            ...s,
            sourceId: s.id,
            order_in_song: idx,
          }))
        : resolveSlideSequence(body, slideOrder),
    [body, slideOrder, onChangeBody, parsedSong.slides],
  );

  // Include slot 0 (empty lyrics) so Intro sections before the first slide are aligned too.
  const alignedWithIntro = useMemo(
    () => (chart ? alignSlides(chart, [{ lyrics: "" }, ...slides]) : null),
    [chart, slides],
  );
  const introRuns = alignedWithIntro?.[0]?.after ?? [];
  const aligned = useMemo(
    () => (alignedWithIntro ? alignedWithIntro.slice(1) : null),
    [alignedWithIntro],
  );

  const interactive = Boolean(onSetWordChord);
  const isChordMode = interactive || Boolean(chart);
  const look = useDisplayLook();

  const effectiveKey = chartKey || chart?.key || "G";
  const paletteItems = useMemo(
    () => diatonicChordItemsForKey(effectiveKey, preferFlats),
    [effectiveKey, preferFlats],
  );

  function formatChordForDisplay(symbol: string): string {
    const spelled = preferFlats !== undefined ? respellChord(symbol, preferFlats) : symbol;
    if (displayMode === "numbers") {
      return chordToNashville(spelled, effectiveKey);
    }
    return spelled;
  }

  const [activeWord, setActiveWord] = useState<{
    wordKey: string;
    slideIdx: number;
    lineIdx: number;
    wordIdx: number;
    slideLyrics: string;
    word: string;
    wordStart: number;
    wordEnd: number;
    chordPos: number;
    chord: string | null;
    syllables: WordSyllable[];
    isTrailing?: boolean;
  } | null>(null);
  const [customChord, setCustomChord] = useState("");
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [editingInst, setEditingInst] = useState<{
    slotIdx: number;
    runIdx: number;
    label: string;
    text: string;
  } | null>(null);

  // Slide-level selection & inline lyric editing (used in both Projector Mode and Leader Mode)
  const [selectedSlideIdx, setSelectedSlideIdx] = useState<number | null>(null);
  const [editingSlideIdx, setEditingSlideIdx] = useState<number | null>(null);
  const [editingLyricsDraft, setEditingLyricsDraft] = useState<string>("");

  const suppressNextClickRef = useRef(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!aiPulse || aiPulse.slides.length === 0) return;
    const card = containerRef.current?.querySelector<HTMLElement>("[data-ai-change='true']");
    if (typeof card?.scrollIntoView === "function") {
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [aiPulse]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  useEffect(() => {
    if (!activeWord) return;
    setCustomChord(activeWord.chord ?? "");
    const t = setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => clearTimeout(t);
  }, [activeWord]);

  useEffect(() => {
    if (!activeWord) return;
    function onPointerDown(e: MouseEvent) {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      if (popoverRef.current && popoverRef.current.contains(target)) return;
      const trigger = target.closest("[data-word-key]");
      if (trigger && trigger.getAttribute("data-word-key") === activeWord?.wordKey) {
        return;
      }
      setActiveWord(null);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setActiveWord(null);
        return;
      }
      if (!activeWord || !onSetWordChord) return;
      const inInput = document.activeElement === inputRef.current;

      // Pressing 1-7 when input is empty or unchanged stamps the diatonic chord immediately!
      if (
        /^[1-7]$/.test(e.key) &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        (!inInput || customChord === (activeWord.chord ?? ""))
      ) {
        const digitIdx = Number(e.key) - 1;
        const item = paletteItems[digitIdx];
        if (item) {
          e.preventDefault();
          applyChord(item.chord);
          return;
        }
      }

      // Backspace/Delete removes the chord when input isn't being edited
      if (
        (e.key === "Backspace" || e.key === "Delete") &&
        (!inInput || customChord === "") &&
        activeWord.chord
      ) {
        e.preventDefault();
        applyChord(null);
        return;
      }

      // Up / Down arrows jump to the previous or next lyric line at the closest word offset
      if (
        (e.key === "ArrowDown" || e.key === "ArrowUp") &&
        (!inInput || customChord === (activeWord.chord ?? ""))
      ) {
        if (!containerRef.current) return;
        const lines = Array.from(
          containerRef.current.querySelectorAll<HTMLElement>("[data-testid='chord-line']"),
        );
        const curLineIdx = lines.findIndex((lineEl) =>
          Boolean(lineEl.querySelector(`[data-word-key='${activeWord.wordKey}']`)),
        );
        if (curLineIdx !== -1) {
          const targetLine = lines[curLineIdx + (e.key === "ArrowDown" ? 1 : -1)];
          if (targetLine) {
            const lineBtns = Array.from(
              targetLine.querySelectorAll<HTMLButtonElement>("[data-testid='chord-word-btn']"),
            );
            if (lineBtns.length > 0) {
              e.preventDefault();
              let bestBtn = lineBtns[0]!;
              let bestDist = Infinity;
              for (const btn of lineBtns) {
                const wrap = btn.closest("[data-word-start]");
                const start = Number(wrap?.getAttribute("data-word-start") ?? 0);
                const dist = Math.abs(start - activeWord.wordStart);
                if (dist < bestDist) {
                  bestDist = dist;
                  bestBtn = btn;
                }
              }
              bestBtn.click();
            }
          }
        }
        return;
      }

      // Tab / Shift+Tab or ArrowLeft / ArrowRight navigates across word buttons
      if (
        e.key === "Tab" ||
        ((e.key === "ArrowRight" || e.key === "ArrowLeft") &&
          (!inInput || customChord === (activeWord.chord ?? "")))
      ) {
        if (!containerRef.current) return;
        const buttons = Array.from(
          containerRef.current.querySelectorAll<HTMLButtonElement>("[data-testid='chord-word-btn']"),
        );
        const curIdx = buttons.findIndex(
          (b) => b.getAttribute("data-word-key") === activeWord.wordKey,
        );
        if (curIdx !== -1) {
          const delta = e.key === "ArrowLeft" || (e.key === "Tab" && e.shiftKey) ? -1 : 1;
          const nextBtn = buttons[curIdx + delta];
          if (nextBtn) {
            e.preventDefault();
            nextBtn.click();
          }
        }
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWord, customChord, paletteItems]);

  // Keyboard navigation across 16:9 Projector slide cards when in non-chord mode
  useEffect(() => {
    if (isChordMode || selectedSlideIdx === null || editingSlideIdx !== null) return;
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
        const s = slides[selectedSlideIdx!];
        if (s && onChangeBody) {
          e.preventDefault();
          setEditingSlideIdx(selectedSlideIdx);
          setEditingLyricsDraft(s.lyrics);
        }
        return;
      }
      if (e.key === "Escape") {
        setSelectedSlideIdx(null);
        return;
      }
      if (
        e.key === "ArrowRight" ||
        e.key === "ArrowLeft" ||
        e.key === "ArrowDown" ||
        e.key === "ArrowUp"
      ) {
        if (slides.length === 0) return;
        e.preventDefault();
        const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1;
        const nextIdx = Math.max(0, Math.min(slides.length - 1, selectedSlideIdx! + step));
        setSelectedSlideIdx(nextIdx);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isChordMode, selectedSlideIdx, editingSlideIdx, slides, onChangeBody]);

  // Pointer-based chord dragging across syllables, letters, or words within a slide
  useEffect(() => {
    if (!dragState) return;
    function onPointerMove(e: PointerEvent) {
      setDragState((prev) => {
        if (!prev) return null;
        const dist = Math.hypot(e.clientX - prev.startX, e.clientY - prev.startY);
        const moved = prev.moved || dist >= 4;
        if (!moved) return prev;

        const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
        const sylEl = el?.closest("[data-syllable-pos]") as HTMLElement | null;
        if (sylEl) {
          const slideIdx = Number(sylEl.getAttribute("data-slide-idx"));
          const lineIdx = Number(sylEl.getAttribute("data-line-idx"));
          const sylPos = Number(sylEl.getAttribute("data-syllable-pos"));
          if (slideIdx === prev.slideIdx && Number.isFinite(lineIdx) && Number.isFinite(sylPos)) {
            if (prev.toLineIdx === lineIdx && prev.toPos === sylPos && prev.moved === moved) {
              return prev;
            }
            return { ...prev, moved: true, toLineIdx: lineIdx, toPos: sylPos };
          }
        }
        const charEl = el?.closest("[data-char-pos]") as HTMLElement | null;
        if (charEl) {
          const slideIdx = Number(charEl.getAttribute("data-slide-idx"));
          const lineIdx = Number(charEl.getAttribute("data-line-idx"));
          const charPos = Number(charEl.getAttribute("data-char-pos"));
          if (slideIdx === prev.slideIdx && Number.isFinite(lineIdx) && Number.isFinite(charPos)) {
            if (prev.toLineIdx === lineIdx && prev.toPos === charPos && prev.moved === moved) {
              return prev;
            }
            return { ...prev, moved: true, toLineIdx: lineIdx, toPos: charPos };
          }
        }
        const wordEl = el?.closest("[data-word-start]") as HTMLElement | null;
        if (wordEl) {
          const slideIdx = Number(wordEl.getAttribute("data-slide-idx"));
          const lineIdx = Number(wordEl.getAttribute("data-line-idx"));
          const wordStart = Number(wordEl.getAttribute("data-word-start"));
          if (slideIdx === prev.slideIdx && Number.isFinite(lineIdx) && Number.isFinite(wordStart)) {
            return { ...prev, moved: true, toLineIdx: lineIdx, toPos: wordStart };
          }
        }
        return prev.moved === moved ? prev : { ...prev, moved };
      });
    }

    function onPointerUp() {
      setDragState((prev) => {
        if (prev && prev.moved) {
          suppressNextClickRef.current = true;
          setTimeout(() => {
            suppressNextClickRef.current = false;
          }, 50);
          if (
            onMoveChord &&
            (prev.fromLineIdx !== prev.toLineIdx || prev.fromPos !== prev.toPos)
          ) {
            setActiveWord(null);
            onMoveChord(
              prev.slideLyrics,
              prev.fromLineIdx,
              prev.fromPos,
              prev.toLineIdx,
              prev.toPos,
              prev.chord,
            );
          }
        }
        return null;
      });
    }

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [dragState, onMoveChord]);

  const resolvedCustomPreview = useMemo(() => {
    const trimmed = customChord.trim();
    if (!trimmed) return null;
    const resolved = resolveChordInput(trimmed, effectiveKey, preferFlats);
    const nash = chordToNashville(resolved, effectiveKey);
    return { resolved, nash };
  }, [customChord, effectiveKey, preferFlats]);

  function commitSlidesMutation(nextList: OverrideSlide[]) {
    if (!onChangeBody) return;
    if (nextList.length === 0) {
      onChangeBody("");
      return;
    }
    onChangeBody(slidesToBody(nextList));
  }

  function handleAddSlide() {
    if (!onChangeBody) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    const last = currentList[currentList.length - 1];
    const nextSection: ParsedSection = last
      ? last.section
      : { type: "verse", number: 1 };
    const nextIdx = currentList.length;
    commitSlidesMutation([...currentList, { section: nextSection, lyrics: NEW_SLIDE_STAND_IN }]);
    setSelectedSlideIdx(nextIdx);
    setEditingSlideIdx(nextIdx);
    setEditingLyricsDraft("");
  }

  // An empty slide can't exist in the song text, so while a slide's box is
  // empty the song holds a stand-in (the box itself starts empty, with the
  // stand-in only as a hint).
  function handleDraftLyrics(idx: number, draft: string) {
    setEditingLyricsDraft(draft);
    handleUpdateSlideLyrics(idx, draft.trim() ? draft : NEW_SLIDE_STAND_IN);
  }

  // ...and once nobody is typing in it, a slide still holding the stand-in
  // is removed, however the editing ended (Escape, a click on another card,
  // a click away), with the selection kept on the same card.
  useEffect(() => {
    if (!onChangeBody) return;
    const empty = slides
      .map((slide, i) => (slide.lyrics === NEW_SLIDE_STAND_IN && i !== editingSlideIdx ? i : -1))
      .filter((i) => i >= 0);
    if (empty.length === 0) return;
    const before = (idx: number) => empty.filter((i) => i < idx).length;
    commitSlidesMutation(
      slides
        .filter((_, i) => !empty.includes(i))
        .map((slide) => ({ section: slide.section, lyrics: slide.lyrics })),
    );
    if (selectedSlideIdx !== null) {
      setSelectedSlideIdx(empty.includes(selectedSlideIdx) ? null : selectedSlideIdx - before(selectedSlideIdx));
    }
    if (editingSlideIdx !== null) setEditingSlideIdx(editingSlideIdx - before(editingSlideIdx));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slides, editingSlideIdx]);

  function handleUpdateSlideLyrics(idx: number, nextLyrics: string) {
    if (!onChangeBody) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    if (!currentList[idx]) return;
    currentList[idx] = { ...currentList[idx]!, lyrics: nextLyrics };
    commitSlidesMutation(currentList);
  }

  function handleUpdateSlideSection(idx: number, nextSection: ParsedSection) {
    if (!onChangeBody) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    if (!currentList[idx]) return;
    currentList[idx] = { ...currentList[idx]!, section: nextSection };
    commitSlidesMutation(currentList);
  }

  function handleDuplicateSlide(idx: number) {
    if (!onChangeBody) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    const target = currentList[idx];
    if (!target) return;
    const nextList = [
      ...currentList.slice(0, idx + 1),
      { section: { ...target.section }, lyrics: target.lyrics },
      ...currentList.slice(idx + 1),
    ];
    commitSlidesMutation(nextList);
    setSelectedSlideIdx(idx + 1);
  }

  function handleSplitSlide(idx: number) {
    if (!onChangeBody || slides[idx]?.lyrics === NEW_SLIDE_STAND_IN) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    const nextList = splitAt(currentList, idx, undefined, { midLine: !hasChords });
    if (nextList === currentList) return;
    onBeforeSplit?.();
    if (editingSlideIdx === idx) setEditingSlideIdx(null);
    commitSlidesMutation(nextList);
    setSelectedSlideIdx(idx);
  }

  function handleDeleteSlide(idx: number) {
    if (!onChangeBody) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    const nextList = currentList.filter((_, i) => i !== idx);
    if (editingSlideIdx === idx) setEditingSlideIdx(null);
    commitSlidesMutation(nextList);
    if (selectedSlideIdx !== null) {
      setSelectedSlideIdx(nextList.length > 0 ? Math.min(selectedSlideIdx, nextList.length - 1) : null);
    }
  }

  function handleDragEndSlides(event: DragEndEvent) {
    if (!onChangeBody) return;
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIdx = slides.findIndex((s) => s.id === active.id);
    const newIdx = slides.findIndex((s) => s.id === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    const currentList: OverrideSlide[] = slides.map((s) => ({
      section: s.section,
      lyrics: s.lyrics,
    }));
    commitSlidesMutation(arrayMove(currentList, oldIdx, newIdx));
    setSelectedSlideIdx(newIdx);
  }

  if (slides.length === 0) {
    if (onChangeBody && !isChordMode) {
      return (
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          <button
            type="button"
            data-testid="add-first-slide-btn"
            onClick={handleAddSlide}
            className="relative aspect-video w-full overflow-hidden rounded-md border border-dashed border-border bg-muted/20 text-xs text-muted-foreground transition-colors hover:border-primary/60 hover:bg-muted/40 hover:text-foreground"
          >
            <span className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 px-4 text-center">
              <Plus className="size-5 text-primary" />
              <span className="font-medium text-foreground">Add first slide</span>
              <span className="text-[11px] text-muted-foreground">
                Or click &ldquo;Raw text&rdquo; above to paste a full song
              </span>
            </span>
          </button>
        </div>
      );
    }
    return (
      <div className="flex h-full items-center justify-center py-12 text-sm text-muted-foreground">
        Type lyrics to see a preview.
      </div>
    );
  }

  function applyChord(rawNextChord: string | null, opts?: { keepOpen?: boolean }) {
    if (!activeWord || !onSetWordChord) return;
    const resolved = rawNextChord
      ? resolveChordInput(rawNextChord, effectiveKey, preferFlats)
      : null;
    onSetWordChord(
      activeWord.slideLyrics,
      activeWord.lineIdx,
      activeWord.wordStart,
      activeWord.wordEnd,
      activeWord.chordPos,
      resolved || null,
    );
    if (opts?.keepOpen) {
      setActiveWord((prev) => (prev ? { ...prev, chord: resolved || null } : null));
    } else {
      setActiveWord(null);
    }
  }

  function selectSyllableInPopover(syl: WordSyllable, lineChords: PlacedChord[]) {
    if (!activeWord) return;
    const existingOnSyl = lineChords.filter((c) => c.pos >= syl.start && c.pos < syl.end);
    const sylChordStr =
      existingOnSyl.length > 0 ? existingOnSyl.map((c) => c.chord).join(" ") : null;
    setActiveWord({
      ...activeWord,
      wordStart: syl.start,
      wordEnd: syl.end,
      chordPos: existingOnSyl[0]?.pos ?? syl.start,
      chord: sylChordStr,
    });
  }

  function commitInstrumentalEdit() {
    if (!editingInst || !onSetInstrumental) return;
    const tokens = editingInst.text
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map((tok) => resolveChordInput(tok, effectiveKey, preferFlats));
    onSetInstrumental(editingInst.slotIdx, editingInst.runIdx, editingInst.label, tokens);
    setEditingInst(null);
  }

  // --- NON-CHORD MODE: 16:9 Projector Slide Grid ---
  if (!isChordMode) {
    const slideIds = slides.map((s) => s.id);
    return (
      <div ref={containerRef} className="flex flex-col gap-3">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEndSlides}
        >
          <SortableContext items={slideIds} strategy={rectSortingStrategy}>
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {slides.map((slide, i) => {
                const slideSuggestions = suggestions.filter((s) => s.slideIdx === i);
                const pulsing = aiPulse?.slides.includes(i) ?? false;
                return (
                  <div key={slide.id} className="relative" data-ai-change={pulsing ? "true" : undefined}>
                  <ProjectorSlideCard
                    slide={slide}
                    look={look}
                    editable={Boolean(onChangeBody)}
                    selected={selectedSlideIdx === i}
                    editing={editingSlideIdx === i}
                    editingDraft={editingSlideIdx === i ? editingLyricsDraft : slide.lyrics}
                    slideSuggestions={slideSuggestions}
                    activeSuggestionId={activeSuggestionId}
                    onAcceptSuggestion={onAcceptSuggestion}
                    onSelect={() => {
                      setSelectedSlideIdx(i);
                      if (editingSlideIdx !== null && editingSlideIdx !== i) {
                        handleDraftLyrics(editingSlideIdx, editingLyricsDraft);
                        setEditingSlideIdx(null);
                      }
                    }}
                    onStartEdit={() => {
                      if (!onChangeBody) return;
                      setSelectedSlideIdx(i);
                      setEditingSlideIdx(i);
                      setEditingLyricsDraft(slide.lyrics);
                    }}
                    onChangeDraft={(val) => handleDraftLyrics(i, val)}
                    onStopEdit={() => {
                      setEditingSlideIdx(null);
                    }}
                    onChangeSection={(sec) => handleUpdateSlideSection(i, sec)}
                    onDuplicate={() => handleDuplicateSlide(i)}
                    onSplit={() => handleSplitSlide(i)}
                    splitMidLine={!hasChords}
                    onDelete={() => handleDeleteSlide(i)}
                  />
                  {pulsing && aiPulse && <AiChangeGlow key={aiPulse.token} />}
                  </div>
                );
              })}
              {onChangeBody && (
                <button
                  type="button"
                  data-testid="add-slide-card-btn"
                  onClick={handleAddSlide}
                  className="relative aspect-video w-full overflow-hidden rounded-md border border-dashed border-border text-xs text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
                >
                  <span className="absolute inset-0 flex items-center justify-center gap-1.5">
                    <Plus className="size-4" />
                    Add slide
                  </span>
                </button>
              )}
            </div>
          </SortableContext>
        </DndContext>
      </div>
    );
  }

  // --- CHORD MODE: Leader Sheet & Chord Editor (Responsive 1-2 Column Layout) ---
  return (
    <div ref={containerRef} className="flex flex-col gap-3">
      {interactive && (
        <div
          data-testid="chord-shortcut-legend"
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-sky-500/25 bg-sky-500/10 px-3.5 py-2 text-xs text-sky-200"
        >
          <span className="font-medium text-sky-300">
            Click any word or syllable to place a chord &middot; Drag any chord to move it
          </span>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-sky-200/80">
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded border border-sky-400/30 bg-sky-950/60 px-1 py-0.5 font-mono text-[10px] text-sky-200">
                1–7
              </kbd>
              Number chord
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded border border-sky-400/30 bg-sky-950/60 px-1 py-0.5 font-mono text-[10px] text-sky-200">
                Tab
              </kbd>
              <kbd className="rounded border border-sky-400/30 bg-sky-950/60 px-1 py-0.5 font-mono text-[10px] text-sky-200">
                ←/→/↑/↓
              </kbd>
              Move
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded border border-sky-400/30 bg-sky-950/60 px-1 py-0.5 font-mono text-[10px] text-sky-200">
                Enter
              </kbd>
              Custom / <code className="text-sky-300">6m</code> <code className="text-sky-300">1/3</code>
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded border border-sky-400/30 bg-sky-950/60 px-1 py-0.5 font-mono text-[10px] text-sky-200">
                ⌫
              </kbd>
              Remove
            </span>
          </div>
        </div>
      )}

      {/* Intro / Pre-Song Instrumental Section */}
      {(introRuns.length > 0 || (interactive && onSetInstrumental)) && (
        <div className="flex flex-col gap-2">
          {introRuns.map((run, ri) => {
            const chordsFlat = run.lines.flatMap((l) => l.chords.map((c) => c.chord));
            const isEditingThis =
              editingInst?.slotIdx === 0 && editingInst?.runIdx === ri;
            return (
              <div
                key={`intro-${ri}`}
                data-testid="instrumental-section-card"
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
                    {run.label || "Intro"}
                  </span>
                  {isEditingThis ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        commitInstrumentalEdit();
                      }}
                      className="flex flex-wrap items-center gap-1.5"
                    >
                      <select
                        value={editingInst.label}
                        onChange={(e) =>
                          setEditingInst({ ...editingInst, label: e.target.value })
                        }
                        aria-label="Instrumental section type"
                        className="h-7 rounded border border-input bg-background px-1.5 text-xs text-foreground"
                      >
                        {INSTRUMENTAL_LABELS.map((lbl) => (
                          <option key={lbl} value={lbl}>
                            {lbl}
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        value={editingInst.text}
                        onChange={(e) =>
                          setEditingInst({ ...editingInst, text: e.target.value })
                        }
                        placeholder="Chords or numbers (e.g. G D Em C or 1 5 6m 4)"
                        data-testid="instrumental-chords-input"
                        autoFocus
                        className="h-7 w-56 rounded border border-input bg-background px-2 font-mono text-xs text-foreground"
                      />
                      <button
                        type="submit"
                        data-testid="instrumental-save-btn"
                        className="h-7 rounded bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingInst(null)}
                        className="h-7 rounded px-2 text-xs text-muted-foreground hover:text-foreground"
                      >
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <div className="flex flex-wrap items-center gap-1.5">
                      {chordsFlat.map((ch, ci) => (
                        <span
                          key={ci}
                          data-testid="instrumental-chord-chip"
                          className="rounded bg-amber-500/15 px-2 py-0.5 font-mono text-xs font-bold text-amber-300"
                        >
                          {formatChordForDisplay(ch)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {interactive && onSetInstrumental && !isEditingThis && (
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() =>
                        setEditingInst({
                          slotIdx: 0,
                          runIdx: ri,
                          label: run.label || "Intro",
                          text: chordsFlat.join(" "),
                        })
                      }
                      data-testid="edit-instrumental-btn"
                      className="rounded px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => onSetInstrumental(0, ri, run.label || "Intro", [])}
                      data-testid="remove-instrumental-btn"
                      title="Remove instrumental section"
                      className="rounded p-1 text-muted-foreground hover:bg-destructive/15 hover:text-destructive"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {interactive && onSetInstrumental && introRuns.length === 0 && (
            editingInst?.slotIdx === 0 ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  commitInstrumentalEdit();
                }}
                className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2"
              >
                <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
                  Add
                </span>
                <select
                  value={editingInst.label}
                  onChange={(e) =>
                    setEditingInst({ ...editingInst, label: e.target.value })
                  }
                  aria-label="Instrumental section type"
                  className="h-7 rounded border border-input bg-background px-1.5 text-xs text-foreground"
                >
                  {INSTRUMENTAL_LABELS.map((lbl) => (
                    <option key={lbl} value={lbl}>
                      {lbl}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  value={editingInst.text}
                  onChange={(e) =>
                    setEditingInst({ ...editingInst, text: e.target.value })
                  }
                  placeholder="Chords or numbers (e.g. G D Em C or 1 5 6m 4)"
                  data-testid="instrumental-chords-input"
                  autoFocus
                  className="h-7 w-64 rounded border border-input bg-background px-2 font-mono text-xs text-foreground"
                />
                <button
                  type="submit"
                  data-testid="instrumental-save-btn"
                  className="h-7 rounded bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                >
                  Add
                </button>
                <button
                  type="button"
                  onClick={() => setEditingInst(null)}
                  className="h-7 rounded px-2 text-xs text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div className="flex items-center">
                <button
                  type="button"
                  data-testid="add-intro-btn"
                  onClick={() =>
                    setEditingInst({
                      slotIdx: 0,
                      runIdx: -1,
                      label: "Intro",
                      text: "",
                    })
                  }
                  className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-amber-500/40 hover:bg-amber-500/10 hover:text-amber-300"
                >
                  <Plus className="size-3" />
                  Add Intro chords
                </button>
              </div>
            )
          )}
        </div>
      )}

      {/* Leader Sheet Section Cards in 1-2 Column Grid on wide screens */}
      <div className="grid grid-cols-1 items-start gap-3.5 xl:grid-cols-2">
        {slides.map((slide, i) => {
          const slideAligned = aligned?.[i];
          const rawLines = slide.lyrics.split("\n");
          const linesToRender = slideAligned
            ? slideAligned.lines
            : rawLines.map((text) => ({ text, chords: [] as PlacedChord[] }));
          const isEditingThisSlideLyrics = editingSlideIdx === i;
          const slideSuggestions = suggestions.filter((s) => s.slideIdx === i);
          const slideChordSuggestions = slideSuggestions.filter(
            (s) => s.category === "chord" || s.category === "chord-fix",
          );
          const slideLyricSuggestion = slideSuggestions.find(
            (s) => s.category === "lyric-fix",
          );
          const slideSplitSuggestion = slideSuggestions.find(
            (s) => s.category === "slide-split",
          );

          return (
            <div
              key={slide.id}
              className="relative rounded-lg border border-border bg-muted/25 p-3.5 shadow-2xs"
              data-testid="preview-slide"
              data-ai-change={aiPulse?.slides.includes(i) ? "true" : undefined}
            >
              {aiPulse?.slides.includes(i) && <AiChangeGlow key={aiPulse.token} />}
              <div className="mb-2 flex items-center justify-between gap-2 border-b border-border/50 pb-1.5">
                <div className={`text-xs font-semibold uppercase tracking-wider ${sectionLabelClass(slide.section.type)}`}>
                  {sectionLabel(slide.section)}
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  {onChangeBody && (
                    <button
                      type="button"
                      data-testid={`edit-lyrics-in-chords-btn-${i}`}
                      onClick={() => {
                        if (isEditingThisSlideLyrics) {
                          setEditingSlideIdx(null);
                        } else {
                          setEditingSlideIdx(i);
                          setEditingLyricsDraft(slide.lyrics);
                        }
                      }}
                      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs transition-colors ${
                        isEditingThisSlideLyrics
                          ? "bg-sky-500/20 text-sky-300"
                          : "text-muted-foreground hover:bg-accent hover:text-foreground"
                      }`}
                      title="Edit this section's lyrics inline (chords stay attached to words)"
                    >
                      {isEditingThisSlideLyrics ? (
                        <>
                          <Check className="size-3" />
                          Done
                        </>
                      ) : (
                        <>
                          <Pencil className="size-3" />
                          Edit lyrics
                        </>
                      )}
                    </button>
                  )}
                  {interactive && onSetInstrumental && (
                    <button
                      type="button"
                      data-testid={`add-turnaround-btn-${i}`}
                      onClick={() =>
                        setEditingInst({
                          slotIdx: i + 1,
                          runIdx: -1,
                          label: i === slides.length - 1 ? "Outro" : "Turnaround",
                          text: "",
                        })
                      }
                      className="inline-flex items-center gap-0.5 rounded px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-amber-500/10 hover:text-amber-300"
                      title="Add instrumental turnaround, interlude, or outro chords after this section"
                    >
                      <Plus className="size-3" />
                      {i === slides.length - 1 ? "Outro / Turnaround" : "Turnaround"}
                    </button>
                  )}
                </div>
              </div>

              {isEditingThisSlideLyrics ? (
                <div className="space-y-1.5">
                  <Textarea
                    autoFocus
                    value={editingLyricsDraft}
                    onChange={(e) => {
                      setEditingLyricsDraft(e.target.value);
                      handleUpdateSlideLyrics(i, e.target.value);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        e.preventDefault();
                        setEditingSlideIdx(null);
                      }
                    }}
                    data-testid="inline-chord-lyrics-textarea"
                    className="min-h-[96px] resize-y font-sans text-base leading-relaxed"
                  />
                  <div className="text-[11px] text-muted-foreground">
                    Press <kbd className="rounded border px-1 font-mono text-[10px]">Esc</kbd> or click Done when finished. Chords stay attached to matching words.
                  </div>
                </div>
              ) : interactive ? (
                <div className="space-y-2 text-base leading-relaxed text-foreground" data-testid="preview-chords">
                  {linesToRender.map((line, li) => {
                    // Apply live drag preview + accidental spelling
                    let effectiveChords = line.chords.map((c) => ({
                      ...c,
                      chord: preferFlats !== undefined ? respellChord(c.chord, preferFlats) : c.chord,
                    }));
                    if (dragState?.moved && dragState.slideIdx === i) {
                      if (dragState.fromLineIdx === li) {
                        effectiveChords = effectiveChords.filter((c) => c.pos !== dragState.fromPos);
                      }
                      if (dragState.toLineIdx === li) {
                        effectiveChords = [
                          ...effectiveChords.filter((c) => c.pos !== dragState.toPos),
                          { pos: dragState.toPos, chord: dragState.chord },
                        ];
                      }
                    }

                    const { tokens, trailingChords } = tokenizeLineWithSegments(
                      line.text,
                      effectiveChords,
                    );
                    if (tokens.length === 0 && trailingChords.length === 0) {
                      return <div key={li} className="h-3" />;
                    }
                    const tailStart = line.text.length + 1;
                    const maxTailPos =
                      trailingChords.length > 0
                        ? Math.max(...trailingChords.map((c) => c.pos))
                        : line.text.length;
                    const nextTailPos = Math.max(tailStart, maxTailPos + 3);
                    const tailAddKey = `${i}-${li}-tail-add`;
                    const isTailAnyOpen =
                      activeWord?.slideIdx === i &&
                      activeWord?.lineIdx === li &&
                      Boolean(activeWord?.isTrailing);

                    return (
                      <div key={li} className="group/line flex flex-wrap items-end gap-y-1" data-testid="chord-line">
                        {tokens.map((tok, wi) => {
                          const wordKey = `${i}-${li}-${wi}`;
                          const isOpen = activeWord?.wordKey === wordKey;
                          const ghostSuggestion = slideChordSuggestions.find((s) => {
                            if (s.lineIdx !== li || s.chordPos === undefined) return false;
                            if (s.wordStart !== undefined) {
                              return s.wordStart === tok.start;
                            }
                            return s.chordPos >= tok.start && s.chordPos < tok.end;
                          });
                          const isActiveGhost =
                            ghostSuggestion !== undefined &&
                            ghostSuggestion.id === activeSuggestionId;

                          return (
                            <span
                              key={wi}
                              data-slide-idx={i}
                              data-line-idx={li}
                              data-word-start={tok.start}
                              className="relative inline-flex items-end"
                            >
                              {ghostSuggestion && ghostSuggestion.chord && (
                                <button
                                  type="button"
                                  data-testid="ghost-chord-pill"
                                  data-active-suggestion={isActiveGhost ? "true" : "false"}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (onDismissSuggestion) {
                                      onDismissSuggestion(ghostSuggestion);
                                      setActiveWord({
                                        wordKey,
                                        slideIdx: i,
                                        lineIdx: li,
                                        wordIdx: wi,
                                        slideLyrics: slide.lyrics,
                                        word: tok.word,
                                        wordStart: tok.start,
                                        wordEnd: tok.end,
                                        chordPos: tok.primaryChordPos,
                                        chord: tok.primaryChord,
                                        syllables: tok.syllables,
                                      });
                                      return;
                                    }
                                    onAcceptSuggestion?.(ghostSuggestion);
                                  }}
                                  title={
                                    onDismissSuggestion
                                      ? "Click to edit this chord yourself. Tab accepts the bright suggestion."
                                      : isActiveGhost
                                        ? `${ghostSuggestion.reason} — Click or press Tab to accept`
                                        : `${ghostSuggestion.reason} — Later suggestion`
                                  }
                                  className={`absolute -top-0.5 left-0.5 z-20 inline-flex items-center gap-1 rounded border border-dashed px-1.5 py-0.5 font-mono text-xs font-bold leading-none transition-all ${
                                    isActiveGhost
                                      ? "border-amber-400 bg-amber-500/30 text-amber-200 ring-1 ring-amber-400/70 shadow-sm"
                                      : "border-amber-200/25 bg-amber-400/5 text-amber-100/40 shadow-none hover:border-amber-300/45 hover:bg-amber-400/10 hover:text-amber-100/75"
                                  }`}
                                >
                                  <span>
                                    {ghostSuggestion.category === "chord-fix"
                                      ? `→ ${formatChordForDisplay(ghostSuggestion.chord)}`
                                      : formatChordForDisplay(ghostSuggestion.chord)}
                                  </span>
                                  {isActiveGhost && (
                                    <kbd className="rounded bg-amber-950/80 px-1 font-sans text-[9px] font-semibold text-amber-200">
                                      Tab
                                    </kbd>
                                  )}
                                </button>
                              )}
                              <button
                                type="button"
                                data-word-key={wordKey}
                                data-testid="chord-word-btn"
                                onClick={() => {
                                  if (suppressNextClickRef.current) return;
                                  if (ghostSuggestion && onDismissSuggestion) {
                                    onDismissSuggestion(ghostSuggestion);
                                  }
                                  setActiveWord((prev) =>
                                    prev?.wordKey === wordKey
                                      ? null
                                      : {
                                          wordKey,
                                          slideIdx: i,
                                          lineIdx: li,
                                          wordIdx: wi,
                                          slideLyrics: slide.lyrics,
                                          word: tok.word,
                                          wordStart: tok.start,
                                          wordEnd: tok.end,
                                          chordPos: tok.primaryChordPos,
                                          chord: tok.primaryChord,
                                          syllables: tok.syllables,
                                        },
                                  );
                                }}
                                className={`group/word inline-flex items-end rounded px-0.5 text-left transition-colors ${
                                  isOpen
                                    ? "bg-sky-500/20 ring-1 ring-sky-400/60"
                                    : "hover:bg-accent/60"
                                }`}
                                title={
                                  tok.primaryChord
                                    ? `Chord: ${tok.primaryChord} (click to edit, drag to move over any syllable or word)`
                                    : `Click to add a chord above "${tok.word}"`
                                }
                              >
                                {tok.segments.map((seg, si) => {
                                  const isDraggedTarget =
                                    dragState?.moved &&
                                    dragState.slideIdx === i &&
                                    dragState.toLineIdx === li &&
                                    dragState.toPos === seg.chordPos &&
                                    seg.chord !== null;
                                  const displayedChord = seg.chord
                                    ? seg.chord
                                        .split(/\s+/)
                                        .map((c) => formatChordForDisplay(c))
                                        .join(" ")
                                    : null;
                                  const isMultiSegmentWord = tok.segments.length > 1;
                                  const nextSegHasChord = Boolean(tok.segments[si + 1]?.chord);
                                  const showMelismaExtender =
                                    isMultiSegmentWord &&
                                    si < tok.segments.length - 1 &&
                                    nextSegHasChord &&
                                    !/\s$/.test(seg.text);

                                  return (
                                    <span
                                      key={si}
                                      className={`inline-flex flex-col items-start ${
                                        isMultiSegmentWord && seg.chord && si < tok.segments.length - 1
                                          ? "pr-1.5"
                                          : ""
                                      }`}
                                    >
                                      <span
                                        onPointerDown={
                                          seg.chord
                                            ? (e) => {
                                                e.stopPropagation();
                                                setDragState({
                                                  slideIdx: i,
                                                  slideLyrics: slide.lyrics,
                                                  fromLineIdx: li,
                                                  fromPos: seg.chordPos,
                                                  chord: seg.chord!,
                                                  toLineIdx: li,
                                                  toPos: seg.chordPos,
                                                  startX: e.clientX,
                                                  startY: e.clientY,
                                                  moved: false,
                                                });
                                              }
                                            : undefined
                                        }
                                        data-testid={seg.chord ? "chord-badge" : undefined}
                                        className={`min-h-[1.3em] whitespace-pre text-sm leading-tight select-none ${
                                          seg.chord
                                            ? isDraggedTarget
                                              ? "cursor-grabbing rounded bg-sky-500 px-1.5 font-bold text-white shadow-sm"
                                              : "cursor-grab rounded bg-sky-500/15 px-1.5 font-bold text-sky-400 group-hover/word:bg-sky-500/25 active:cursor-grabbing"
                                            : si === 0 && !tok.primaryChord
                                              ? "px-0.5 text-xs text-muted-foreground/0 group-hover/word:text-sky-400/80"
                                              : ""
                                        }`}
                                      >
                                        {displayedChord ?? (si === 0 && !tok.primaryChord ? "+" : "\u200b")}
                                      </span>
                                      <span className="inline-flex items-baseline whitespace-pre leading-snug">
                                        {Array.from(seg.text).map((ch, ci) => {
                                          const charPos = seg.segStart + ci;
                                          const matchingSyl = tok.syllables.find(
                                            (s) => charPos >= s.start && charPos < s.end,
                                          );
                                          const isCharTarget =
                                            dragState?.moved &&
                                            dragState.slideIdx === i &&
                                            dragState.toLineIdx === li &&
                                            dragState.toPos === charPos;
                                          return (
                                            <span
                                              key={charPos}
                                              data-slide-idx={i}
                                              data-line-idx={li}
                                              data-char-pos={charPos}
                                              data-syllable-pos={matchingSyl?.start}
                                              className={
                                                isCharTarget
                                                  ? "rounded-sm bg-sky-500/30 text-sky-200 underline decoration-sky-400 decoration-2"
                                                  : undefined
                                              }
                                            >
                                              {ch}
                                            </span>
                                          );
                                        })}
                                        {showMelismaExtender && (
                                          <span
                                            aria-hidden="true"
                                            className="mx-0.5 font-mono text-xs text-muted-foreground/45 select-none"
                                          >
                                            &ndash;
                                          </span>
                                        )}
                                      </span>
                                    </span>
                                  );
                                })}
                              </button>

                              {isOpen && (
                                <ChordPickerPopover
                                  popoverRef={popoverRef}
                                  inputRef={inputRef}
                                  activeWord={activeWord}
                                  lineChords={effectiveChords}
                                  paletteItems={paletteItems}
                                  displayMode={displayMode}
                                  customChord={customChord}
                                  setCustomChord={setCustomChord}
                                  resolvedCustomPreview={resolvedCustomPreview}
                                  onSelectSyllable={(syl) => selectSyllableInPopover(syl, effectiveChords)}
                                  onApplyChord={applyChord}
                                />
                              )}
                            </span>
                          );
                        })}

                        {/* Discrete Trailing / Line-End Chord Pills (`[D] [Em] [+]`) */}
                        <span
                          data-slide-idx={i}
                          data-line-idx={li}
                          data-word-start={tailStart}
                          className="relative ml-1 inline-flex items-end gap-1"
                        >
                          {trailingChords.map((tc, tci) => {
                            const itemKey = `${i}-${li}-tail-${tci}`;
                            const isThisTailOpen = activeWord?.wordKey === itemKey;
                            const nextTc = trailingChords[tci + 1];
                            return (
                              <button
                                key={itemKey}
                                type="button"
                                data-word-key={itemKey}
                                data-testid={tci === 0 ? "trailing-chord-btn" : `trailing-chord-btn-${tci}`}
                                onClick={() => {
                                  if (suppressNextClickRef.current) return;
                                  setActiveWord((prev) =>
                                    prev?.wordKey === itemKey
                                      ? null
                                      : {
                                          wordKey: itemKey,
                                          slideIdx: i,
                                          lineIdx: li,
                                          wordIdx: tokens.length + tci,
                                          slideLyrics: slide.lyrics,
                                          word: "line end",
                                          wordStart: tc.pos,
                                          wordEnd: nextTc ? nextTc.pos : tc.pos + 1,
                                          chordPos: tc.pos,
                                          chord: tc.chord,
                                          syllables: [],
                                          isTrailing: true,
                                        },
                                  );
                                }}
                                title={`Trailing chord: ${tc.chord} (click to edit/remove, drag onto any word)`}
                                className={`inline-flex flex-col items-center rounded px-0.5 text-xs transition-colors ${
                                  isThisTailOpen
                                    ? "bg-amber-500/25 ring-1 ring-amber-400/70"
                                    : "hover:bg-accent/60"
                                }`}
                              >
                                <span
                                  onPointerDown={(e) => {
                                    e.stopPropagation();
                                    setDragState({
                                      slideIdx: i,
                                      slideLyrics: slide.lyrics,
                                      fromLineIdx: li,
                                      fromPos: tc.pos,
                                      chord: tc.chord,
                                      toLineIdx: li,
                                      toPos: tc.pos,
                                      startX: e.clientX,
                                      startY: e.clientY,
                                      moved: false,
                                    });
                                  }}
                                  data-testid="trailing-chord-badge"
                                  className="min-h-[1.3em] cursor-grab rounded bg-amber-500/20 px-1.5 font-mono text-sm font-bold leading-tight text-amber-300 select-none active:cursor-grabbing"
                                >
                                  {formatChordForDisplay(tc.chord)}
                                </span>
                                <span className="leading-snug text-muted-foreground/35 select-none">
                                  &bull;
                                </span>
                              </button>
                            );
                          })}

                          {/* Add New Trailing Chord Button (`+ tail` when empty, `+` when trailing chords exist) */}
                          <button
                            type="button"
                            data-word-key={tailAddKey}
                            data-testid={
                              trailingChords.length === 0
                                ? "trailing-chord-btn"
                                : "trailing-chord-add-btn"
                            }
                            onClick={() => {
                              setActiveWord((prev) =>
                                prev?.wordKey === tailAddKey
                                  ? null
                                  : {
                                      wordKey: tailAddKey,
                                      slideIdx: i,
                                      lineIdx: li,
                                      wordIdx: tokens.length + trailingChords.length,
                                      slideLyrics: slide.lyrics,
                                      word: "line end",
                                      wordStart: nextTailPos,
                                      wordEnd: nextTailPos + 20,
                                      chordPos: nextTailPos,
                                      chord: null,
                                      syllables: [],
                                      isTrailing: true,
                                    },
                              );
                            }}
                            title="Add a trailing / passing chord after the end of this line"
                            className={`inline-flex flex-col items-center rounded px-0.5 text-xs transition-opacity ${
                              activeWord?.wordKey === tailAddKey
                                ? "opacity-100"
                                : trailingChords.length > 0
                                  ? "opacity-70 hover:opacity-100"
                                  : "opacity-0 group-hover/line:opacity-100 focus:opacity-100"
                            }`}
                          >
                            <span className="min-h-[1.3em] rounded border border-dashed border-sky-400/40 px-1.5 font-mono text-xs font-normal leading-tight text-sky-400/80 hover:bg-sky-500/15">
                              {trailingChords.length > 0 ? "+" : "+ tail"}
                            </span>
                            <span className="leading-snug text-muted-foreground/40">{"\u00a0"}</span>
                          </button>

                          {isTailAnyOpen && activeWord && (
                            <ChordPickerPopover
                              popoverRef={popoverRef}
                              inputRef={inputRef}
                              activeWord={activeWord}
                              lineChords={effectiveChords}
                              paletteItems={paletteItems}
                              displayMode={displayMode}
                              customChord={customChord}
                              setCustomChord={setCustomChord}
                              resolvedCustomPreview={resolvedCustomPreview}
                              onSelectSyllable={() => {}}
                              onApplyChord={applyChord}
                            />
                          )}
                        </span>
                      </div>
                    );
                  })}

                  {/* Ghost Lyric Fix or Slide Split Prompt on Leader Card */}
                  {slideLyricSuggestion && (
                    <div
                      data-testid="ghost-lyric-fix-pill"
                      className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded border border-dashed border-amber-400/50 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-200"
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Sparkles className="size-3.5 text-amber-400" />
                        <span className="font-medium">{slideLyricSuggestion.label}:</span>
                        {slideLyricSuggestion.lyricDiffPreview && (
                          <span className="font-mono text-[11px] text-amber-300/90">
                            &ldquo;{slideLyricSuggestion.lyricDiffPreview.from}&rdquo; &rarr; &ldquo;
                            {slideLyricSuggestion.lyricDiffPreview.to}&rdquo;
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => onAcceptSuggestion?.(slideLyricSuggestion)}
                        className="inline-flex items-center gap-1 rounded bg-amber-500/25 px-2 py-0.5 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/35"
                      >
                        Accept
                        {slideLyricSuggestion.id === activeSuggestionId && (
                          <kbd className="rounded bg-amber-950/80 px-1 font-mono text-[9px]">Tab</kbd>
                        )}
                      </button>
                    </div>
                  )}

                  {slideSplitSuggestion && (
                    <div
                      data-testid="ghost-slide-split-pill"
                      className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded border border-dashed border-amber-400/50 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-200"
                    >
                      <div className="flex items-center gap-1.5">
                        <Scissors className="size-3.5 text-amber-400" />
                        <span>{slideSplitSuggestion.label}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => onAcceptSuggestion?.(slideSplitSuggestion)}
                        className="inline-flex items-center gap-1 rounded bg-amber-500/25 px-2 py-0.5 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/35"
                      >
                        Split slide
                        {slideSplitSuggestion.id === activeSuggestionId && (
                          <kbd className="rounded bg-amber-950/80 px-1 font-mono text-[9px]">Tab</kbd>
                        )}
                      </button>
                    </div>
                  )}

                  {/* Instrumental / Turnaround runs after this slide */}
                  {slideAligned?.after.map((run, ri) => {
                    const chordsFlat = run.lines.flatMap((l) => l.chords.map((c) => c.chord));
                    const isEditingThis =
                      editingInst?.slotIdx === i + 1 && editingInst?.runIdx === ri;
                    return (
                      <div
                        key={`after-${ri}`}
                        data-testid="instrumental-section-card"
                        className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-500/25 bg-amber-500/5 px-2.5 py-1.5"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
                            {run.label || "Instrumental"}
                          </span>
                          {isEditingThis ? (
                            <form
                              onSubmit={(e) => {
                                e.preventDefault();
                                commitInstrumentalEdit();
                              }}
                              className="flex flex-wrap items-center gap-1.5"
                            >
                              <select
                                value={editingInst.label}
                                onChange={(e) =>
                                  setEditingInst({ ...editingInst, label: e.target.value })
                                }
                                aria-label="Instrumental section type"
                                className="h-6 rounded border border-input bg-background px-1 text-xs text-foreground"
                              >
                                {INSTRUMENTAL_LABELS.map((lbl) => (
                                  <option key={lbl} value={lbl}>
                                    {lbl}
                                  </option>
                                ))}
                              </select>
                              <input
                                type="text"
                                value={editingInst.text}
                                onChange={(e) =>
                                  setEditingInst({ ...editingInst, text: e.target.value })
                                }
                                placeholder="e.g. Em C G D or 6m 4 1 5"
                                data-testid="instrumental-chords-input"
                                autoFocus
                                className="h-6 w-48 rounded border border-input bg-background px-2 font-mono text-xs text-foreground"
                              />
                              <button
                                type="submit"
                                data-testid="instrumental-save-btn"
                                className="h-6 rounded bg-primary px-2 text-xs font-medium text-primary-foreground"
                              >
                                Save
                              </button>
                            </form>
                          ) : (
                            <div className="flex flex-wrap items-center gap-1.5">
                              {chordsFlat.map((ch, ci) => (
                                <span
                                  key={ci}
                                  data-testid="instrumental-chord-chip"
                                  className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-xs font-bold text-amber-300"
                                >
                                  {formatChordForDisplay(ch)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        {onSetInstrumental && !isEditingThis && (
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() =>
                                setEditingInst({
                                  slotIdx: i + 1,
                                  runIdx: ri,
                                  label: run.label || "Turnaround",
                                  text: chordsFlat.join(" "),
                                })
                              }
                              data-testid="edit-instrumental-btn"
                              className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                onSetInstrumental(i + 1, ri, run.label || "Turnaround", [])
                              }
                              data-testid="remove-instrumental-btn"
                              className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="size-3" />
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {editingInst?.slotIdx === i + 1 && editingInst.runIdx === -1 && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        commitInstrumentalEdit();
                      }}
                      className="mt-2 flex flex-wrap items-center gap-1.5 rounded border border-amber-500/30 bg-amber-500/5 px-2.5 py-1.5"
                    >
                      <select
                        value={editingInst.label}
                        onChange={(e) =>
                          setEditingInst({ ...editingInst, label: e.target.value })
                        }
                        aria-label="Instrumental section type"
                        className="h-6 rounded border border-input bg-background px-1 text-xs text-foreground"
                      >
                        {INSTRUMENTAL_LABELS.map((lbl) => (
                          <option key={lbl} value={lbl}>
                            {lbl}
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        value={editingInst.text}
                        onChange={(e) =>
                          setEditingInst({ ...editingInst, text: e.target.value })
                        }
                        placeholder="Chords or numbers (e.g. Em C G D or 6m 4 1 5)"
                        data-testid="instrumental-chords-input"
                        autoFocus
                        className="h-6 w-52 rounded border border-input bg-background px-2 font-mono text-xs text-foreground"
                      />
                      <button
                        type="submit"
                        data-testid="instrumental-save-btn"
                        className="h-6 rounded bg-primary px-2 text-xs font-medium text-primary-foreground"
                      >
                        Add
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingInst(null)}
                        className="h-6 rounded px-1.5 text-xs text-muted-foreground hover:text-foreground"
                      >
                        Cancel
                      </button>
                    </form>
                  )}
                </div>
              ) : (
                <div className="text-base leading-relaxed text-foreground" data-testid="preview-chords">
                  {aligned?.[i]?.lines.map((line, li) => (
                    <ChordLyrics
                      key={li}
                      text={line.text}
                      chords={line.chords.map((c) => ({
                        ...c,
                        chord: formatChordForDisplay(c.chord),
                      }))}
                    />
                  ))}
                  {aligned?.[i]?.after.map((run, ri) => (
                    <div key={`after-${ri}`} className="mt-1">
                      {run.lines.map((line, li) => (
                        <ChordLyrics
                          key={li}
                          text={line.text}
                          chords={line.chords.map((c) => ({
                            ...c,
                            chord: formatChordForDisplay(c.chord),
                          }))}
                        />
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
