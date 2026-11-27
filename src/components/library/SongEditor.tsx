import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  History,
  Check,
  Code2,
  FileUp,
  Mic,
  Music,
  Plus,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from "lucide-react";

import {
  createSong,
  deleteSong,
  getSetlist,
  listSetlists,
  deleteSongChart,
  getSong,
  getSongCharts,
  importCharts,
  listLibraries,
  saveSongReference,
  updateSong,
  type NewSongInput,
  type SongRow,
} from "@/lib/tauri";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { SongHistoryDialog } from "@/components/library/SongHistoryDialog";
import { Textarea } from "@/components/ui/textarea";
import { SongPreview, type ChordDisplayMode } from "@/components/library/SongPreview";
import { parseSong, sectionLabel, sectionLabelClass, type ParsedSection } from "@/lib/parseSong";
import { formatSlideOrder, parseSlideOrder } from "@/lib/slideSequence";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { useDisplayLook } from "@/lib/displayLook";
import { deleteSongMessage } from "@/lib/deleteSongText";
import {
  alignSlides,
  chartHasChords,
  emptyChart,
  parseStoredChart,
  type ChordChart,
} from "@/lib/chords/chart";
import { keyPrefersFlats } from "@/lib/chords/chord";
import { changedSlideIndexes } from "@/lib/chords/voiceChart";
import { useChordAi } from "@/lib/chords/useChordAi";
import { useVoiceChart, type VoiceTake } from "@/lib/chords/useVoiceChart";
import { FEATURES } from "@/lib/features";
import {
  fromChordText,
  moveChordInBody,
  respellChartAndText,
  setInstrumentalChordsInBody,
  setWordChordInBody,
  stepTransposeChartAndText,
  toChordText,
  transposeChartAndText,
} from "@/lib/chords/editorText";
import {
  decodeBytes,
  importChartFile,
  SONG_FILE_EXTENSIONS,
  type ImportedLeadSheet,
} from "@/lib/chords/import";
import { readLeadSheetWithEngine } from "@/lib/chords/import/leadSheet";
import { importPdfFile } from "@/lib/chords/import/pdf";
import { chartToSong } from "@/lib/chords/import/toSong";
import { fetchChordSuggestions } from "@/lib/chords/chordModel";
import {
  applyAllSuggestions,
  applySingleSuggestion,
  computeSongSuggestions,
  getExtraArrangementGhostSections,
  type EditContext,
  type SongSuggestion,
  type SuggestionSnapshot,
} from "@/lib/chords/suggestions";
import { KeyBadge } from "@/components/KeyBadge";
import { KeySignaturePicker } from "@/components/library/KeySignaturePicker";

interface Props {
  songId: string | null; // null = creating a new song
  onClose: (savedId: string | null) => void;
  /** Where the back button goes, for its tooltip. */
  backLabel?: string;
}

const AUTOSAVE_DEBOUNCE_MS = 1_000;

type SaveState = "idle" | "saving" | "saved" | "error";

async function readFileBytes(file: File): Promise<Uint8Array> {
  if (typeof file.arrayBuffer === "function") {
    return new Uint8Array(await file.arrayBuffer());
  }
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (result instanceof ArrayBuffer) {
        resolve(new Uint8Array(result));
      } else {
        resolve(new Uint8Array());
      }
    };
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read file"));
    reader.readAsArrayBuffer(file);
  });
}

/** The time signatures worship songs are written in; another one an import
 *  brought in still shows. */
const TIME_SIGNATURES = ["4/4", "3/4", "6/8", "12/8", "2/4", "2/2", "5/4", "9/8"];

export function SongEditor({ songId, onClose, backLabel = "Library" }: Props) {
  const toast = useToast();
  const look = useDisplayLook();
  const confirm = useConfirm();
  // Earlier words are kept for an organization's library (its server keeps them).
  const [inOrgLibrary, setInOrgLibrary] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => {
    let live = true;
    listLibraries().then(
      (l) => live && setInOrgLibrary(l.find((x) => x.open)?.kind === "organization"),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [author, setAuthor] = useState("");
  const [ccli, setCcli] = useState("");
  const [slideOrder, setSlideOrder] = useState("");
  const [orderOpen, setOrderOpen] = useState(false);
  const [loading, setLoading] = useState(songId !== null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Chords: the song's chart, and the "chords" view of the body that edits it.
  const [chart, setChart] = useState<ChordChart | null>(null);
  const [showChords, setShowChords] = useState(false);
  const [rawTextMode, setRawTextMode] = useState(false);
  const [displayMode, setDisplayMode] = useState<ChordDisplayMode>("letters");
  const [chordText, setChordText] = useState("");
  const [preferFlats, setPreferFlats] = useState(false);
  const [copyright, setCopyright] = useState<string | null>(null);
  const [fillingFromPdf, setFillingFromPdf] = useState(false);
  const [aiPulse, setAiPulse] = useState<{ token: number; slides: number[] } | null>(null);
  // Hidden with no Groq key or with the AI switched off.
  const chordAi = useChordAi();
  const bodyRef = useRef(body);
  const chordTextRef = useRef(chordText);
  const slideOrderRef = useRef(slideOrder);
  const preferFlatsRef = useRef(preferFlats);
  bodyRef.current = body;
  chordTextRef.current = chordText;
  slideOrderRef.current = slideOrder;
  preferFlatsRef.current = preferFlats;
  // Set only after a voice take writes chords, so Undo drops that take from
  // the revision history and a suggestion undo does not.
  const undoDropsVoiceTurn = useRef(false);
  const voice = useVoiceChart({
    enabled: chordAi,
    slideEdits: FEATURES.voiceSlideEdits,
    read: () => ({ body: bodyRef.current, chart: chartRef.current }),
    apply: applyVoiceTake,
    onError: (message) => toast.error(message),
  });
  const resetVoice = voice.reset;
  // AI / Smart Suggestions ("Suggest" explicit mode + "Cursor Tab" reactive mode)
  const [suggestMode, setSuggestMode] = useState<"off" | "explicit" | "reactive">("off");
  const [lastEdit, setLastEdit] = useState<EditContext | null>(null);
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => new Set());
  const [modelSuggestions, setModelSuggestions] = useState<SongSuggestion[]>([]);
  const [undoSnapshot, setUndoSnapshot] = useState<SuggestionSnapshot | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pendingLeadSheetRef = useRef<ImportedLeadSheet | null>(null);
  // Read at save time, so a debounced save never writes a stale chart.
  const chartRef = useRef<ChordChart | null>(null);
  chartRef.current = chart;
  const chartDirtyRef = useRef(false);
  const hadChartRef = useRef(false);

  // Track current id (becomes non-null after first autosave on a new song)
  const idRef = useRef<string | null>(songId);
  const dirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    resetVoice();
    setAiPulse(null);
    undoDropsVoiceTurn.current = false;
    if (songId === null) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const song = await getSong(songId);
        if (cancelledRef.current) return;
        const loadedBody = song?.body_text ?? "";
        if (song) {
          setTitle(song.title);
          setBody(loadedBody);
          setAuthor(song.author ?? "");
          setCcli(song.ccli ?? "");
          setCopyright(song.copyright ?? null);
          setSlideOrder(song.slide_order ?? "");
        }
        const charts = await getSongCharts([songId]).catch(() => ({}));
        if (cancelledRef.current) return;
        const stored = parseStoredChart((charts as Record<string, { chart_json: string }>)[songId]?.chart_json);
        setChart(stored);
        setChordText(toChordText(loadedBody, stored));
        if (stored?.key) setPreferFlats(keyPrefersFlats(stored.key));
        hadChartRef.current = stored !== null;
      } catch (err) {
        if (!cancelledRef.current) {
          setErrorMsg(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    })();
    return () => {
      cancelledRef.current = true;
    };
  }, [songId, resetVoice]);

  // Debounced autosave
  useEffect(() => {
    if (loading) return;
    if (!dirtyRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      void persist();
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, body, author, ccli, copyright, slideOrder, chart, loading]);

  // Flush pending save on unmount
  useEffect(() => {
    return () => {
      if (dirtyRef.current) void persist();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function markDirty() {
    dirtyRef.current = true;
    setSaveState("idle");
  }

  async function persist() {
    if (!dirtyRef.current) return;
    if (title.trim() === "" && body.trim() === "") {
      // Don't autosave totally empty drafts
      return;
    }
    setSaveState("saving");
    const input: NewSongInput = {
      title: title.trim() || "Untitled",
      body_text: body,
      author: author.trim() || null,
      ccli: ccli.trim() || null,
      copyright: copyright?.trim() || null,
      slide_order: slideOrder.trim() || null,
    };
    try {
      if (idRef.current === null) {
        const newId = await createSong(input);
        idRef.current = newId;
      } else {
        await updateSong(idRef.current, input);
      }
      if (chartDirtyRef.current && idRef.current !== null) {
        const current = chartRef.current;
        // A chart with only a key, tempo or time signature is kept too:
        // library search reads them ("fast songs", "in 6/8").
        if (current && (chartHasChords(current) || current.key || current.tempo || current.time)) {
          await importCharts([
            {
              existing_song_id: idRef.current,
              song: null,
              chart_json: JSON.stringify(current),
              original_key: current.key,
              source_format: "editor",
            },
          ]);
          hadChartRef.current = true;
        } else if (hadChartRef.current) {
          // Every chord and fact was removed: the song has no chart any more.
          await deleteSongChart(idRef.current);
          hadChartRef.current = false;
        }
        chartDirtyRef.current = false;
      }
      if (pendingLeadSheetRef.current && idRef.current !== null) {
        const ls = pendingLeadSheetRef.current;
        pendingLeadSheetRef.current = null;
        await saveSongReference({
          song_id: idRef.current,
          kind: "lead_sheet",
          source_name: ls.title ?? null,
          data_json: ls.scoreJson,
          summary_json: JSON.stringify({
            key: ls.key,
            barCount: ls.barCount,
            readerVersion: ls.readerVersion,
          }),
        }).catch(() => {});
      }
      dirtyRef.current = false;
      setSaveState("saved");
      setErrorMsg(null);
    } catch (err) {
      setSaveState("error");
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDelete() {
    if (idRef.current === null) {
      onClose(null);
      return;
    }
    const songId = idRef.current;
    const usedBy: string[] = [];
    for (const sl of await listSetlists().catch(() => [])) {
      const detail = await getSetlist(sl.id).catch(() => null);
      if (detail?.items.some((i) => i.song_id === songId)) usedBy.push(sl.name);
    }
    const ok = await confirm({
      title: `Delete "${title || "this song"}"?`,
      message: deleteSongMessage(usedBy),
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    dirtyRef.current = false;
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    try {
      await deleteSong(idRef.current);
      toast.success(`Deleted "${title || "song"}".`);
      onClose(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setErrorMsg(message);
      toast.error(`Delete failed: ${message}`);
    }
  }

  function toggleChords() {
    if (!showChords) setChordText(toChordText(body, chart));
    setShowChords((v) => !v);
    if (rawTextMode) setRawTextMode(false);
  }

  function toggleRawTextMode() {
    if (!rawTextMode) {
      setChordText(toChordText(body, chart));
    }
    setRawTextMode((v) => !v);
  }

  /**
   * An edit in the raw text view (which always supports chords-over-lyrics and
   * inline `[G]` chords): split into the plain `body` and the updated `chart`.
   */
  function editRawChordText(text: string) {
    setChordText(text);
    const next = fromChordText(text, chart);
    setBody(next.body);
    if (next.chart || chartHasChords(chart ?? emptyChart())) {
      setChart(
        next.chart ?? (chart?.key ? { ...emptyChart(), key: chart.key } : null),
      );
      chartDirtyRef.current = true;
    }
    markDirty();
  }

  /**
   * An edit to the slide lyrics/sections from the Visual Slide Editor.
   * Preserves any existing `chart` (since `alignSlides` aligns by word stream).
   */
  function handleChangeBodyFromVisual(nextBody: string) {
    const prevSlides = parseSong(body).slides;
    const nextSlides = parseSong(nextBody).slides;
    let changedIdx = 0;
    for (let i = 0; i < nextSlides.length; i++) {
      if (!prevSlides[i] || prevSlides[i]!.lyrics !== nextSlides[i]!.lyrics) {
        changedIdx = i;
        break;
      }
    }
    setBody(nextBody);
    setChordText(toChordText(nextBody, chart));
    if (nextSlides.length === prevSlides.length) {
      setLastEdit({ kind: "lyric", slideIdx: changedIdx });
      setSuggestMode((m) => (m === "off" ? "reactive" : m));
    }
    markDirty();
  }

  function handleSetWordChord(
    slideLyrics: string,
    lineIdx: number,
    wordStart: number,
    wordEnd: number,
    chordPos: number,
    nextChord: string | null,
  ) {
    const next = setWordChordInBody(
      body,
      chart,
      slideLyrics,
      lineIdx,
      wordStart,
      wordEnd,
      chordPos,
      nextChord,
    );
    const nextChart =
      next.chart ?? (chart?.key ? { ...emptyChart(), key: chart.key } : null);
    setChordText(next.chordText);
    setChart(nextChart);
    chartDirtyRef.current = true;
    if (nextChord) {
      const slideIdx = Math.max(
        0,
        parseSong(body).slides.findIndex((s) => s.lyrics === slideLyrics),
      );
      setLastEdit({
        kind: "chord",
        slideIdx,
        lineIdx,
        chordPos,
        chord: nextChord,
      });
      setSuggestMode((m) => (m === "off" ? "reactive" : m));
    }
    markDirty();
  }

  function handleSetInstrumental(
    slotIdx: number,
    runIdxInSlot: number,
    label: string,
    chords: string[],
  ) {
    const next = setInstrumentalChordsInBody(
      body,
      chart,
      slotIdx,
      runIdxInSlot,
      label,
      chords,
    );
    setChordText(next.chordText);
    setChart(
      next.chart ?? (chart?.key ? { ...emptyChart(), key: chart.key } : null),
    );
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleKeyChange(nextKey: string, shouldTranspose: boolean) {
    if (!nextKey) {
      const base = chart ?? emptyChart();
      setChart({ ...base, key: null });
      chartDirtyRef.current = true;
      markDirty();
      return;
    }
    const nextFlats = keyPrefersFlats(nextKey);
    setPreferFlats(nextFlats);
    if (shouldTranspose && chart && chartHasChords(chart) && chart.key) {
      const transposed = transposeChartAndText(body, chart, nextKey, nextFlats);
      setChordText(transposed.chordText);
      setChart(transposed.chart);
    } else {
      const base = chart ?? emptyChart();
      setChart({ ...base, key: nextKey });
    }
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleTempoChange(value: string) {
    const n = Math.round(Number(value));
    const tempo = value.trim() === "" || !Number.isFinite(n) || n <= 0 ? null : Math.min(n, 300);
    setChart({ ...(chart ?? emptyChart()), tempo });
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleTimeChange(value: string) {
    setChart({ ...(chart ?? emptyChart()), time: value || null });
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleStepTranspose(semitones: number) {
    const stepped = stepTransposeChartAndText(body, chart, semitones);
    setPreferFlats(stepped.preferFlats);
    setChordText(stepped.chordText);
    setChart(stepped.chart ?? { ...emptyChart(), key: stepped.nextKey });
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleMoveChord(
    slideLyrics: string,
    fromLineIdx: number,
    fromPos: number,
    toLineIdx: number,
    toPos: number,
    chordSymbol: string,
  ) {
    const next = moveChordInBody(
      body,
      chart,
      slideLyrics,
      fromLineIdx,
      fromPos,
      toLineIdx,
      toPos,
      chordSymbol,
    );
    setChordText(next.chordText);
    setChart(
      next.chart ?? (chart?.key ? { ...emptyChart(), key: chart.key } : null),
    );
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleToggleAccidentals(nextFlats: boolean) {
    setPreferFlats(nextFlats);
    if (chart) {
      const respelled = respellChartAndText(body, chart, nextFlats);
      setChordText(respelled.chordText);
      setChart(respelled.chart);
      chartDirtyRef.current = true;
      markDirty();
    }
  }

  async function handleFillFromPdfFile(file: File) {
    setFillingFromPdf(true);
    try {
      const bytes = await readFileBytes(file);
      const isPdf =
        file.name.toLowerCase().endsWith(".pdf") ||
        (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46);

      const res = isPdf
        ? await importPdfFile(file.name, bytes)
        : importChartFile(file.name, decodeBytes(bytes));

      if (isPdf) {
        void readLeadSheetWithEngine(file.name, bytes).then((leadCheck) => {
          if (leadCheck?.kind === "lead_sheet") {
            pendingLeadSheetRef.current = leadCheck.sheet;
            markDirty();
          }
        });
      }

      const importedSong = res.songs[0];
      if (!importedSong) {
        if (isPdf) {
          const leadCheck = await readLeadSheetWithEngine(file.name, bytes);
          if (leadCheck?.kind === "lead_sheet") {
            pendingLeadSheetRef.current = leadCheck.sheet;
            const ls = leadCheck.sheet;
            if (ls.title && (!title.trim() || title.trim() === "Untitled")) setTitle(ls.title);
            if (ls.ccli) setCcli(ls.ccli);
            if (ls.copyright) setCopyright(ls.copyright);
            if (ls.key) {
              const nextFlats = keyPrefersFlats(ls.key);
              setPreferFlats(nextFlats);
              const nextChart = { ...(chart ?? emptyChart()), key: ls.key };
              setChart(nextChart);
              chartDirtyRef.current = true;
            }
            markDirty();
            toast.success(`Filled lead sheet details from "${file.name}".`);
            return;
          }
        }
        toast.error(res.error ?? "No lyrics or chords found in this file.");
        return;
      }

      const converted = chartToSong(importedSong);
      let nextBody = body;
      let nextSlideOrder = slideOrder;

      if (body.trim() === "" && converted.body_text.trim() !== "") {
        nextBody = converted.body_text;
        setBody(nextBody);
        if (!slideOrder.trim() && converted.slide_order) {
          nextSlideOrder = converted.slide_order;
          setSlideOrder(nextSlideOrder);
        }
      } else if (body.trim() !== "" && converted.body_text.trim() !== "") {
        const existingSlides = parseSong(body).slides.map((s) => ({ lyrics: s.lyrics }));
        const aligned = alignSlides(importedSong.chart, existingSlides);
        const anyMatched = aligned.some((a) => a.matched);
        if (!anyMatched) {
          nextBody = converted.body_text;
          setBody(nextBody);
          if (converted.slide_order) {
            nextSlideOrder = converted.slide_order;
            setSlideOrder(nextSlideOrder);
          }
        } else if (!slideOrder.trim() && converted.slide_order) {
          nextSlideOrder = converted.slide_order;
          setSlideOrder(nextSlideOrder);
        }
      }

      const resolvedKey =
        importedSong.chart.key ??
        chart?.key ??
        null;
      const nextChart: ChordChart = {
        ...importedSong.chart,
        key: resolvedKey,
      };

      setChart(nextChart);
      chartRef.current = nextChart;
      chartDirtyRef.current = true;
      if (resolvedKey) {
        setPreferFlats(keyPrefersFlats(resolvedKey));
      }
      setChordText(toChordText(nextBody, nextChart));
      setShowChords(true);

      const resolvedTitle = importedSong.title || null;
      if (resolvedTitle && (!title.trim() || title.trim() === "Untitled")) {
        setTitle(resolvedTitle);
      }
      if (importedSong.artist) {
        setAuthor(importedSong.artist);
      }
      const resolvedCcli = importedSong.ccli ?? null;
      if (resolvedCcli) {
        setCcli(resolvedCcli);
      }
      const resolvedCopyright = importedSong.copyright ?? null;
      if (resolvedCopyright) {
        setCopyright(resolvedCopyright);
      }

      markDirty();
      toast.success(
        `Filled chords${resolvedKey ? ` (Key of ${resolvedKey})` : ""} and song info from "${file.name}".`,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't fill from file: ${message}`);
    } finally {
      setFillingFromPdf(false);
    }
  }

  const parsedSong = useMemo(() => parseSong(body), [body]);
  const derivedOrder = useMemo(
    () => formatSlideOrder(parsedSong.section_order),
    [parsedSong.section_order],
  );
  const activeOrderSections = useMemo<ParsedSection[]>(() => {
    const custom = parseSlideOrder(slideOrder);
    return custom.length > 0 ? custom : parsedSong.section_order;
  }, [slideOrder, parsedSong.section_order]);

  function handleAppendSectionToOrder(sec: ParsedSection) {
    const next = [...activeOrderSections, sec];
    setSlideOrder(formatSlideOrder(next));
    markDirty();
  }

  function handleRemoveOrderStep(idx: number) {
    const next = activeOrderSections.filter((_, i) => i !== idx);
    setSlideOrder(next.length > 0 ? formatSlideOrder(next) : "");
    markDirty();
  }

  // --- AI / Smart Suggestions ("Suggest" explicit mode + "Cursor Tab" reactive mode) ---
  const allAvailableSuggestions = useMemo(
    () =>
      computeSongSuggestions({
        body,
        chart,
        chartKey: chart?.key,
        preferFlats,
        slideOrder,
        showChords,
        mode: "all",
        lastEdit,
      }),
    [body, chart, preferFlats, slideOrder, showChords, lastEdit],
  );

  const activeSuggestions = useMemo<SongSuggestion[]>(() => {
    if (suggestMode === "off" || rawTextMode) return [];
    const raw =
      suggestMode === "explicit"
        ? allAvailableSuggestions
        : computeSongSuggestions({
            body,
            chart,
            chartKey: chart?.key,
            preferFlats,
            slideOrder,
            showChords,
            mode: "reactive",
            lastEdit,
          });
    const filtered = raw.filter((s) => !dismissedIds.has(s.id));
    if (suggestMode !== "reactive") return filtered;
    // Exact later copies stay instant. The model may add the next chord, or a
    // verified phrase, without duplicating a slot the local copy already filled.
    const seen = new Set(filtered.map((s) => `${s.slideIdx}:${s.lineIdx}:${s.chordPos}`));
    const fromModel = modelSuggestions.filter(
      (s) => !dismissedIds.has(s.id) && !seen.has(`${s.slideIdx}:${s.lineIdx}:${s.chordPos}`),
    );
    return [...filtered, ...fromModel];
  }, [
    suggestMode,
    rawTextMode,
    allAvailableSuggestions,
    body,
    chart,
    preferFlats,
    slideOrder,
    showChords,
    lastEdit,
    dismissedIds,
    modelSuggestions,
  ]);

  const activeSuggestion = activeSuggestions[0] ?? null;

  useEffect(() => {
    // Any edit moves the words the last reply pointed at, so drop it now
    // rather than leave a ghost on the wrong syllable until the next reply.
    setModelSuggestions([]);
    if (!chordAi || !FEATURES.chordModelSuggestions || suggestMode !== "reactive" || rawTextMode || !showChords || lastEdit?.kind !== "chord") {
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchChordSuggestions(body, chart, chart?.key, lastEdit.slideIdx)
        .then((next) => {
          if (!cancelled) setModelSuggestions(next);
        })
        .catch(() => {
          if (!cancelled) setModelSuggestions([]);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [chordAi, suggestMode, rawTextMode, showChords, lastEdit, body, chart]);

  const arrangementGhostSections = useMemo(
    () =>
      getExtraArrangementGhostSections(
        body,
        slideOrder,
        activeSuggestions.find((s) => s.category === "arrangement"),
      ),
    [body, slideOrder, activeSuggestions],
  );

  function captureUndoSnapshot(): SuggestionSnapshot {
    undoDropsVoiceTurn.current = false;
    return {
      body,
      chordText,
      chart,
      slideOrder,
      preferFlats,
    };
  }

  function handleRestoreUndoSnapshot() {
    if (!undoSnapshot) return;
    setBody(undoSnapshot.body);
    setChordText(undoSnapshot.chordText);
    setChart(undoSnapshot.chart);
    chartRef.current = undoSnapshot.chart;
    setSlideOrder(undoSnapshot.slideOrder);
    setPreferFlats(undoSnapshot.preferFlats);
    chartDirtyRef.current = true;
    setUndoSnapshot(null);
    if (undoDropsVoiceTurn.current) {
      undoDropsVoiceTurn.current = false;
      voice.dropLastTurn();
    }
    markDirty();
  }

  function handleDismissSuggestion(suggestion: SongSuggestion) {
    setDismissedIds((prev) => {
      const next = new Set(prev);
      next.add(suggestion.id);
      return next;
    });
  }

  function handleAcceptSuggestionList(list: SongSuggestion[], keepOpen: boolean) {
    if (list.length === 0) return;
    setUndoSnapshot(captureUndoSnapshot());
    const next = applyAllSuggestions(list, {
      body,
      chart,
      slideOrder,
      preferFlats,
    });
    setBody(next.body);
    setChordText(next.chordText);
    setChart(next.chart);
    chartRef.current = next.chart;
    setSlideOrder(next.slideOrder);
    if (next.showChords) setShowChords(true);
    chartDirtyRef.current = true;
    if (keepOpen) {
      const last = [...list]
        .reverse()
        .find((s) => s.category === "chord" || s.category === "chord-fix");
      if (last) {
        setLastEdit({
          kind: "chord",
          slideIdx: last.slideIdx ?? 0,
          lineIdx: last.lineIdx,
          chordPos: last.chordPos,
          chord: last.chord,
        });
      }
    } else {
      setSuggestMode("off");
      toast.success(`Applied ${list.length} suggestion${list.length === 1 ? "" : "s"}.`);
    }
    markDirty();
  }

  function handleAcceptSuggestion(suggestion: SongSuggestion) {
    setUndoSnapshot(captureUndoSnapshot());
    const next = applySingleSuggestion(suggestion, {
      body,
      chart,
      slideOrder,
      preferFlats,
    });
    setBody(next.body);
    setChordText(next.chordText);
    setChart(next.chart);
    chartRef.current = next.chart;
    setSlideOrder(next.slideOrder);
    if (next.showChords) setShowChords(true);
    chartDirtyRef.current = true;

    // Update `lastEdit` so reactive "Cursor Tab" continues predicting the next step!
    if (suggestion.category === "chord" || suggestion.category === "chord-fix") {
      setLastEdit({
        kind: "chord",
        slideIdx: suggestion.slideIdx ?? 0,
        lineIdx: suggestion.lineIdx,
        chordPos: suggestion.chordPos,
        chord: suggestion.chord,
      });
    } else if (suggestion.category === "slide-split") {
      setLastEdit({
        kind: "slide-split",
        slideIdx: suggestion.slideIdx ?? 0,
      });
    } else if (suggestion.category === "lyric-fix") {
      setLastEdit({
        kind: "lyric",
        slideIdx: suggestion.slideIdx ?? 0,
      });
    }
    markDirty();
  }

  function handleAcceptSlideSuggestions(slideIdx: number) {
    const forSlide = activeSuggestions.filter(
      (s) => s.slideIdx === slideIdx && (s.category === "chord" || s.category === "chord-fix"),
    );
    if (forSlide.length === 0) return;
    setUndoSnapshot(captureUndoSnapshot());
    const next = applyAllSuggestions(forSlide, {
      body,
      chart,
      slideOrder,
      preferFlats,
    });
    setBody(next.body);
    setChordText(next.chordText);
    setChart(next.chart);
    chartRef.current = next.chart;
    setSlideOrder(next.slideOrder);
    if (next.showChords) setShowChords(true);
    chartDirtyRef.current = true;
    markDirty();
  }

  function handleAcceptAllSuggestions() {
    handleAcceptSuggestionList(activeSuggestions, false);
  }

  function applyVoiceTake(take: VoiceTake) {
    const touched = changedSlideIndexes(bodyRef.current, take.body, chartRef.current, take.chart);
    setAiPulse({
      token: Date.now(),
      slides: touched.length > 0 ? touched : parseSong(take.body).slides.map((_, index) => index),
    });
    setUndoSnapshot({
      body: bodyRef.current,
      chordText: chordTextRef.current,
      chart: chartRef.current,
      slideOrder: slideOrderRef.current,
      preferFlats: preferFlatsRef.current,
    });
    undoDropsVoiceTurn.current = true;
    bodyRef.current = take.body;
    chordTextRef.current = take.chordText;
    chartRef.current = take.chart;
    setBody(take.body);
    setChordText(take.chordText);
    setChart(take.chart);
    setShowChords(true);
    setRawTextMode(false);
    chartDirtyRef.current = true;
    markDirty();
    toast.success(take.transcript ? `Heard “${take.transcript}”` : "Applied that");
  }

  function handleToggleSuggest() {
    if (suggestMode === "explicit" && activeSuggestions.length > 0) {
      setSuggestMode("off");
      return;
    }
    setDismissedIds(new Set());
    if (rawTextMode) setRawTextMode(false);
    // If the song has chords or only needs chord suggestions, ensure chords are visible when needed
    const allWithChords = computeSongSuggestions({
      body,
      chart,
      chartKey: chart?.key,
      preferFlats,
      slideOrder,
      showChords: true,
      mode: "all",
      lastEdit,
    });
    if (allWithChords.length === 0) {
      toast.show("Song looks clean — no chord, slide, or arrangement suggestions right now.");
      setSuggestMode("off");
      return;
    }
    const hasChordSug = allWithChords.some(
      (s) => s.category === "chord" || s.category === "chord-fix",
    );
    if (hasChordSug && !showChords) {
      setShowChords(true);
    }
    setSuggestMode("explicit");
  }

  // Global "Cursor Tab" keyboard handler when ghost suggestions are visible
  useEffect(() => {
    if (activeSuggestions.length === 0 || rawTextMode) return;
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable ||
          Boolean(target.closest?.("[data-testid='chord-picker-popover']")))
      ) {
        return;
      }
      if (e.key === "Tab" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const nextSug = activeSuggestions[0];
        if (nextSug) {
          e.preventDefault();
          if (nextSug.phraseGroup) {
            handleAcceptSuggestionList(
              activeSuggestions.filter((s) => s.phraseGroup === nextSug.phraseGroup),
              true,
            );
          } else {
            handleAcceptSuggestion(nextSug);
          }
        }
      } else if (e.key === "Escape") {
        setSuggestMode("off");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSuggestions, rawTextMode, body, chart, slideOrder, preferFlats]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading song…
      </div>
    );
  }

  const showHarmonyControls = showChords || rawTextMode;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-background px-6 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onClose(idRef.current)}
            title={`Back to ${backLabel}`}
            aria-label="Close"
            className="h-9 w-9 shrink-0 text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
          </Button>
          <Input
            data-testid="song-title"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              markDirty();
            }}
            placeholder="Untitled song"
            className="h-9 max-w-md text-base font-semibold"
          />
          {!showHarmonyControls && chart?.key && (
            <KeyBadge songKey={chart.key} className="ml-1 size-6 text-[11px]" />
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <SaveIndicator state={saveState} />
          {idRef.current !== null && (
            <Menu label="More options">
              {inOrgLibrary && (
                <MenuItem icon={<History />} onSelect={() => setHistoryOpen(true)}>
                  Earlier words…
                </MenuItem>
              )}
              <MenuItem icon={<Trash2 />} onSelect={() => void handleDelete()} destructive>
                Delete song…
              </MenuItem>
            </Menu>
          )}
        </div>
      </header>

      {historyOpen && idRef.current && (
        <SongHistoryDialog
          songId={idRef.current}
          onClose={() => setHistoryOpen(false)}
          onRestore={(words) => {
            setBody(words);
            setChordText(toChordText(words, chart));
            markDirty();
          }}
        />
      )}

      {errorMsg && (
        <div className="border-b border-destructive/40 bg-destructive/10 px-6 py-2 text-xs text-destructive">
          {errorMsg}
        </div>
      )}

      {/* Harmony & Editor Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/30 px-6 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-2.5">
          <Button
            variant={showChords && !rawTextMode ? "default" : "outline"}
            size="sm"
            className="h-8 gap-1.5"
            onClick={toggleChords}
            data-testid="toggle-chords"
            title="Switch between 16:9 Projector Slides and Leader Chord Sheet."
          >
            <Music className="size-3.5" />
            {showChords ? "Hide chords" : "Show chords"}
          </Button>

          {showHarmonyControls && (
            <>
              {/* Key & Transpose Control Group */}
              <div className="flex items-center gap-1.5 rounded-md border border-border/80 bg-background/60 px-2 py-0.5">
                <KeySignaturePicker
                  value={chart?.key ?? ""}
                  preferFlats={preferFlats}
                  hasChords={Boolean(chart && chartHasChords(chart))}
                  onChange={(nextKey, shouldTranspose) =>
                    handleKeyChange(nextKey, shouldTranspose)
                  }
                />
                <div
                  className="inline-flex items-center gap-0.5 border-l border-border/70 pl-1.5"
                  title="Transpose key and all chords down or up by 1 semitone"
                >
                  <button
                    type="button"
                    data-testid="transpose-down"
                    onClick={() => handleStepTranspose(-1)}
                    className="h-6 rounded px-1.5 font-mono text-[11px] font-semibold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    title="Transpose down 1 semitone (-1)"
                  >
                    &minus;1
                  </button>
                  <button
                    type="button"
                    data-testid="transpose-up"
                    onClick={() => handleStepTranspose(1)}
                    className="h-6 rounded px-1.5 font-mono text-[11px] font-semibold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    title="Transpose up 1 semitone (+1)"
                  >
                    +1
                  </button>
                </div>
              </div>

              {/* Tempo & time signature: library search reads them ("fast
                  songs", "in 6/8", "fits with …"); importers fill them. */}
              <div className="flex items-center gap-1.5 rounded-md border border-border/80 bg-background/60 px-2 py-0.5">
                <label className="inline-flex items-center gap-1 text-muted-foreground" title="Tempo in beats per minute">
                  <input
                    type="number"
                    inputMode="numeric"
                    min={30}
                    max={240}
                    data-testid="chart-tempo"
                    aria-label="Tempo (bpm)"
                    placeholder="—"
                    value={chart?.tempo ?? ""}
                    onChange={(e) => handleTempoChange(e.target.value)}
                    className="h-6 w-12 rounded border border-input bg-background px-1 text-right font-mono text-[11px] text-foreground [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  bpm
                </label>
                <select
                  data-testid="chart-time"
                  aria-label="Time signature"
                  title="Time signature"
                  value={chart?.time ?? ""}
                  onChange={(e) => handleTimeChange(e.target.value)}
                  className="h-6 rounded border border-input bg-background px-1 font-mono text-[11px]"
                >
                  <option value="">time</option>
                  {TIME_SIGNATURES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                  {chart?.time && !TIME_SIGNATURES.includes(chart.time) && <option value={chart.time}>{chart.time}</option>}
                </select>
              </div>

              {/* Sharps / Flats Toggle */}
              <div
                role="group"
                aria-label="Sharps or flats"
                data-testid="accidental-toggle"
                className="inline-flex h-8 items-center rounded-md border border-input bg-background p-0.5"
              >
                <button
                  type="button"
                  onClick={() => handleToggleAccidentals(false)}
                  data-testid="accidental-sharps"
                  title="Spell chords and keys with sharps (♯)"
                  className={`inline-flex h-full items-center justify-center rounded px-2.5 font-mono text-base font-extrabold leading-none transition-colors ${
                    !preferFlats
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  &#9839;
                </button>
                <button
                  type="button"
                  onClick={() => handleToggleAccidentals(true)}
                  data-testid="accidental-flats"
                  title="Spell chords and keys with flats (♭)"
                  className={`inline-flex h-full items-center justify-center rounded px-2.5 font-mono text-base font-extrabold leading-none transition-colors ${
                    preferFlats
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  &#9837;
                </button>
              </div>

              {/* Chords vs Nashville Numbers (1-7) Display Toggle (Visual Chord Mode) */}
              {!rawTextMode && (
                <div
                  role="group"
                  aria-label="Chord display notation"
                  data-testid="chord-notation-toggle"
                  className="inline-flex h-8 items-center rounded-md border border-input bg-background p-0.5 text-xs"
                >
                  <button
                    type="button"
                    data-testid="notation-letters"
                    onClick={() => setDisplayMode("letters")}
                    title="Show standard chord names (G, C, Em, D)"
                    className={`h-full rounded px-2.5 text-xs font-medium transition-colors ${
                      displayMode === "letters"
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    Chords <span className="ml-0.5 font-mono opacity-75">G C</span>
                  </button>
                  <button
                    type="button"
                    data-testid="notation-numbers"
                    onClick={() => setDisplayMode("numbers")}
                    title="Show Nashville Number System (1, 4, 6m, 5)"
                    className={`h-full rounded px-2.5 text-xs font-medium transition-colors ${
                      displayMode === "numbers"
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    Numbers <span className="ml-0.5 font-mono opacity-75">1 4</span>
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Suggest (AI / Next-Edit) + Fill from PDF / Chord Sheet + Independent Raw Text Mode Toggle */}
        <div className="flex items-center gap-2">
          {undoSnapshot && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              data-testid="undo-suggestion-btn"
              onClick={handleRestoreUndoSnapshot}
              title="Undo the last change (a split, a deleted slide or an accepted suggestion)"
              className="h-8 gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <Undo2 className="size-3.5" />
              Undo
            </Button>
          )}
          <Button
            type="button"
            variant={suggestMode === "explicit" && activeSuggestions.length > 0 ? "default" : "outline"}
            size="sm"
            data-testid="suggest-btn"
            disabled={!body.trim()}
            onClick={handleToggleSuggest}
            title="Scan song for smart chord completions, cross-verse chord mirroring, slide breath splits, and arrangement order"
            className="h-8 gap-1.5 text-xs"
          >
            <Sparkles className="size-3.5 text-amber-400" />
            Suggest
          </Button>
          {chordAi && (
            <Button
              type="button"
              variant={voice.phase === "recording" ? "default" : "outline"}
              size="sm"
              data-testid="voice-chart-btn"
              disabled={!body.trim() || voice.phase === "working"}
              onPointerDown={voice.warm}
              onClick={() => void voice.toggle()}
              title={
                FEATURES.voiceSlideEdits
                  ? "Record. A finished sentence applies after a two-second pause. Stop ends the session. Try “verse 3 is actually a chorus” or “the verses are 1 4 5 1”."
                  : "Record. A finished sentence applies after a two-second pause. Stop ends the session. Try “the verses are 1 4 5 1” or “hold the 5 for two bars at the end of the chorus”."
              }
              className="h-8 gap-1.5 text-xs"
            >
              <Mic className="size-3.5" />
              {voice.phase === "recording" ? "Stop" : voice.phase === "working" ? "Charting…" : "Voice"}
            </Button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept={SONG_FILE_EXTENSIONS.join(",")}
            data-testid="fill-from-pdf-input"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) {
                void handleFillFromPdfFile(file);
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid="fill-from-pdf"
            disabled={fillingFromPdf}
            onClick={() => fileInputRef.current?.click()}
            title="Fill chords, lyrics, key, and song metadata from a PDF or chord/lead sheet file"
            className="h-8 gap-1.5 text-xs"
          >
            <FileUp className="size-3.5" />
            {fillingFromPdf ? "Filling…" : "Fill from PDF"}
          </Button>
          <Button
            type="button"
            variant={rawTextMode ? "default" : "outline"}
            size="sm"
            data-testid="toggle-raw-text"
            onClick={toggleRawTextMode}
            title="Edit the song and its chords as text (chords above the words, or [G] inline)"
            className="h-8 gap-1.5 text-xs"
          >
            <Code2 className="size-3.5" />
            {rawTextMode ? "Edit as slides" : "Edit as text"}
          </Button>
        </div>
      </div>

      {/* Suggestion Review Bar (only shown when user explicitly clicks Suggest) */}
      {suggestMode === "explicit" && activeSuggestions.length > 0 && !rawTextMode && activeSuggestion && (
        <div
          data-testid="suggestion-review-bar"
          className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-500/30 bg-amber-500/10 px-6 py-2 text-xs text-amber-200"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/20 px-2 py-0.5 font-semibold text-amber-300">
              <Sparkles className="size-3" />
              {activeSuggestions.length}{" "}
              {activeSuggestions.length === 1 ? "suggestion" : "suggestions"}
            </span>
            <span className="truncate font-medium text-foreground">
              Next: {activeSuggestion.label}
            </span>
            <span className="hidden text-amber-200/75 sm:inline">
              &middot; {activeSuggestion.reason}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              data-testid="accept-next-suggestion-btn"
              onClick={() => handleAcceptSuggestion(activeSuggestion)}
              className="inline-flex h-7 items-center gap-1.5 rounded border border-amber-400/50 bg-amber-500/20 px-2.5 text-xs font-semibold text-amber-100 transition-colors hover:bg-amber-500/30"
            >
              <span>Accept next</span>
              <kbd className="rounded border border-amber-400/40 bg-amber-950/70 px-1 font-mono text-[10px] text-amber-200">
                Tab
              </kbd>
            </button>
            {activeSuggestions.length > 1 && (
              <button
                type="button"
                data-testid="accept-all-suggestions-btn"
                onClick={handleAcceptAllSuggestions}
                className="inline-flex h-7 items-center gap-1 rounded bg-amber-500 px-2.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-amber-400"
              >
                <Check className="size-3.5" />
                Accept all ({activeSuggestions.length})
              </button>
            )}
            <button
              type="button"
              data-testid="dismiss-suggestions-btn"
              onClick={() => setSuggestMode("off")}
              title="Dismiss suggestions (Esc)"
              className="inline-flex h-7 items-center gap-1 rounded px-2 text-xs text-amber-200/75 transition-colors hover:bg-amber-500/15 hover:text-amber-100"
            >
              Dismiss
              <kbd className="rounded border border-amber-400/30 px-1 font-mono text-[10px]">
                Esc
              </kbd>
            </button>
          </div>
        </div>
      )}

      {/* Main Editor Canvas: Either Raw Text Mode or Visual Slide / Leader Sheet Editor */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {rawTextMode ? (
          <Textarea
            value={chordText}
            onChange={(e) => editRawChordText(e.target.value)}
            placeholder={
              "[verse 1]\n G                C         G\nAmazing grace how sweet the sound\n\n[chorus]\n…"
            }
            spellCheck={false}
            data-testid="song-body"
            className="flex-1 resize-none rounded-none border-none px-6 py-4 font-mono text-sm leading-relaxed focus-visible:ring-0"
          />
        ) : (
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {/* Hidden sync textarea so programmatic/test raw-text input works seamlessly even when Visual mode is active */}
            <Textarea
              value={chordText || body}
              onChange={(e) => editRawChordText(e.target.value)}
              placeholder={
                "[verse 1]\nFirst slide line one\nFirst slide line two\n\n[chorus]\n…"
              }
              aria-label="Raw song text"
              tabIndex={-1}
              data-testid="song-body"
              className="sr-only"
            />
            <SongPreview
              body={body}
              look={look}
              slideOrder={slideOrder}
              chart={showChords ? chart : null}
              hasChords={!!chart && chartHasChords(chart)}
              onBeforeChange={() => setUndoSnapshot(captureUndoSnapshot())}
              chartKey={chart?.key ?? null}
              preferFlats={preferFlats}
              displayMode={displayMode}
              onSetWordChord={showChords ? handleSetWordChord : undefined}
              onMoveChord={showChords ? handleMoveChord : undefined}
              onSetInstrumental={showChords ? handleSetInstrumental : undefined}
              onChangeBody={handleChangeBodyFromVisual}
              suggestions={activeSuggestions}
              activeSuggestionId={activeSuggestion?.id ?? null}
              onAcceptSuggestion={handleAcceptSuggestion}
              onDismissSuggestion={handleDismissSuggestion}
              onAcceptSlideSuggestions={handleAcceptSlideSuggestions}
              aiPulse={aiPulse}
            />
          </div>
        )}
      </div>

      {/* Footer: Visual Arrangement Order Bar + Metadata */}
      <div className="flex flex-col gap-2 border-t border-border bg-muted/30 px-6 py-2.5 text-xs">
        {parsedSong.section_order.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border/60 pb-2">
            <span className="mr-1 uppercase tracking-wider text-muted-foreground">
              Order
            </span>
            {orderOpen ? (
              <div className="flex flex-1 items-center gap-2">
                <Input
                  value={slideOrder}
                  onChange={(e) => {
                    setSlideOrder(e.target.value);
                    markDirty();
                  }}
                  placeholder={derivedOrder ? derivedOrder : "e.g. V1, C, V2, C, B, C"}
                  className="h-7 max-w-md font-mono text-xs"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setOrderOpen(false)}
                  className="rounded px-2 py-1 text-xs font-medium text-primary hover:bg-accent"
                >
                  Done
                </button>
                {slideOrder.trim() !== "" && (
                  <button
                    type="button"
                    onClick={() => {
                      setSlideOrder("");
                      setOrderOpen(false);
                      markDirty();
                    }}
                    className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    reset to automatic
                  </button>
                )}
              </div>
            ) : (
              <>
                <div
                  className="flex flex-wrap items-center gap-1"
                  data-testid="arrangement-order-pills"
                >
                  {activeOrderSections.map((sec, idx) => (
                    <span
                      key={`${sec.type}-${sec.number}-${idx}`}
                      className="group/pill inline-flex items-center gap-1 rounded border border-border bg-background/80 px-2 py-0.5 text-[11px] font-medium"
                    >
                      <span className={sectionLabelClass(sec.type)}>
                        {sectionLabel(sec)}
                      </span>
                      {activeOrderSections.length > 1 && (
                        <button
                          type="button"
                          onClick={() => handleRemoveOrderStep(idx)}
                          title={`Remove ${sectionLabel(sec)} from arrangement order`}
                          className="text-muted-foreground/60 hover:text-destructive"
                        >
                          <X className="size-2.5" />
                        </button>
                      )}
                    </span>
                  ))}
                  {arrangementGhostSections.length > 0 && (
                    <button
                      type="button"
                      data-testid="ghost-arrangement-pill"
                      onClick={() => {
                        const arrSug = activeSuggestions.find(
                          (s) => s.category === "arrangement",
                        );
                        if (arrSug) handleAcceptSuggestion(arrSug);
                      }}
                      title="Click or press Tab to accept suggested arrangement repeats"
                      className="inline-flex items-center gap-1 rounded border border-dashed border-amber-400/60 bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-300 transition-colors hover:border-amber-400 hover:bg-amber-500/25"
                    >
                      <Sparkles className="size-3 text-amber-400" />
                      <span>
                        + {arrangementGhostSections.map((s) => sectionLabel(s)).join(" → ")}
                      </span>
                      {activeSuggestion?.category === "arrangement" && (
                        <kbd className="rounded bg-amber-950/80 px-1 font-mono text-[9px] text-amber-200">
                          Tab
                        </kbd>
                      )}
                    </button>
                  )}
                </div>

                {/* Quick "+ Repeat section" menu */}
                <Menu
                  label="Add section to order"
                  placement="above"
                  align="left"
                  trigger={(toggle) => (
                    <button
                      type="button"
                      onClick={toggle}
                      data-testid="add-to-order-btn"
                      className="inline-flex h-6 items-center gap-0.5 rounded border border-dashed border-border px-2 text-[11px] text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
                    >
                      <Plus className="size-3" />
                      Repeat section
                    </button>
                  )}
                >
                  {parsedSong.section_order.map((sec, i) => (
                    <MenuItem
                      key={`${sec.type}-${sec.number}-${i}`}
                      onSelect={() => handleAppendSectionToOrder(sec)}
                    >
                      Add {sectionLabel(sec)}
                    </MenuItem>
                  ))}
                </Menu>

                <button
                  type="button"
                  onClick={() => setOrderOpen(true)}
                  className="ml-1 text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  edit text
                </button>
                {slideOrder.trim() !== "" && (
                  <button
                    type="button"
                    onClick={() => {
                      setSlideOrder("");
                      markDirty();
                    }}
                    className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    reset
                  </button>
                )}
              </>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2">
            <span className="uppercase tracking-wider text-muted-foreground">Author</span>
            <Input
              data-testid="song-author"
              value={author}
              onChange={(e) => {
                setAuthor(e.target.value);
                markDirty();
              }}
              className="h-7 w-44 text-xs"
            />
          </label>
          <label className="flex items-center gap-2">
            <span className="uppercase tracking-wider text-muted-foreground">CCLI</span>
            <Input
              data-testid="song-ccli"
              value={ccli}
              onChange={(e) => {
                setCcli(e.target.value);
                markDirty();
              }}
              className="h-7 w-32 text-xs"
            />
          </label>
        </div>
      </div>
    </div>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === "saving") return <span>Saving…</span>;
  if (state === "saved") return <span>Saved</span>;
  if (state === "error") return <span className="text-destructive">Save failed</span>;
  return null;
}

// Re-export type for testability
export type { SongRow };
