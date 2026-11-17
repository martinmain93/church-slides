/**
 * Golden Recording Editor.
 *
 * Plays back a recording's captured audio (read-only ground truth — no audio
 * editing) with a scrubable playhead, shows the slide transitions on a
 * waveform timeline, and lets the operator drag them to retime, add new ones
 * at the playhead, remove them, and retarget which slide they point at.
 *
 * Edits are saved to a sibling `golden.json` (via `saveGolden`); the raw
 * `recording.json` capture is never mutated.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Pause,
  Play,
  Plus,
  Rewind,
  Save,
  Star,
  Trash2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { SlideStage } from "@/components/presentation/SlideStage";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";
import { getSong, type SongRow } from "@/lib/tauri";
import type { ParsedSlide } from "@/lib/parseSong";
import { resolveSlideSequence } from "@/lib/slideSequence";
import {
  loadRecording,
  loadRecordingAudio,
  saveGolden,
} from "@/lib/recordingClient";
import {
  activeMarkerAt,
  clampTime,
  clampZoom,
  formatTime,
  insertMarker,
  isDirty,
  isOutsideView,
  MAX_ZOOM,
  MIN_ZOOM,
  newMarkerId,
  nextMarkerAt,
  removeMarker,
  retargetMarker,
  retimeMarker,
  slideLabel,
  slidePreview,
  timeToViewFraction,
  toEvents,
  toMarkers,
  viewFractionToTime,
  viewWindow,
  ZOOM_STEP,
  type TimelineMarker,
} from "@/lib/recordings/timeline";
import type { GroundTruthEvent, SetlistItemSnapshot } from "@/types/generated";
import { computePeaks, WaveformCanvas, type Peaks } from "./WaveformCanvas";

interface Props {
  recordingId: string;
  onBack: () => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      duration: number;
      setlistContent: SetlistItemSnapshot[];
      blobUrl: string;
      peaks: Peaks | null;
    };

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function RecordingEditor({ recordingId, onBack }: Props) {
  const toast = useToast();
  const confirm = useConfirm();

  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [original, setOriginal] = useState<GroundTruthEvent[]>([]);
  const [markers, setMarkers] = useState<TimelineMarker[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** Human sign-off, persisted with the golden annotation. */
  const [approved, setApproved] = useState(false);
  const [savedApproved, setSavedApproved] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [saving, setSaving] = useState(false);
  /** song_id -> library row, for titles and full-fidelity lyric rendering. */
  const [songs, setSongs] = useState<Record<string, SongRow>>({});

  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [viewCenter, setViewCenter] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const dragIdRef = useRef<string | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const duration = load.kind === "ready" ? load.duration : 0;
  const dirty = useMemo(
    () => isDirty(original, markers) || approved !== savedApproved,
    [original, markers, approved, savedApproved],
  );

  const view = useMemo(
    () => viewWindow(duration, zoom, viewCenter),
    [duration, zoom, viewCenter],
  );

  // --- load metadata + audio ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoad({ kind: "loading" });

    (async () => {
      try {
        const [meta, audio] = await Promise.all([
          loadRecording(recordingId),
          loadRecordingAudio(recordingId),
        ]);
        if (cancelled) return;
        if (meta.error || !meta.recording) {
          setLoad({ kind: "error", message: meta.error ?? "recording not found" });
          return;
        }
        if (audio.error || !audio.audio_base64) {
          setLoad({ kind: "error", message: audio.error ?? "audio not found" });
          return;
        }

        const bytes = base64ToBytes(audio.audio_base64);
        // Two independent ArrayBuffers: one backs the <audio> blob, one is
        // handed to decodeAudioData (which detaches the buffer it receives).
        const blobBuf = bytes.buffer.slice(0) as ArrayBuffer;
        const decodeBuf = bytes.buffer.slice(0) as ArrayBuffer;
        const blob = new Blob([blobBuf], { type: "audio/wav" });
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;

        // Decode for the waveform (best-effort; playback works regardless).
        let peaks: Peaks | null = null;
        try {
          const AC =
            window.AudioContext ||
            (window as unknown as { webkitAudioContext: typeof AudioContext })
              .webkitAudioContext;
          const ctx = new AC();
          const decoded = await ctx.decodeAudioData(decodeBuf);
          peaks = computePeaks(decoded.getChannelData(0));
          void ctx.close();
        } catch {
          peaks = null;
        }
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }

        const seed = meta.golden_ground_truth ?? meta.recording.ground_truth;
        setOriginal(seed);
        setMarkers(toMarkers(seed));
        setApproved(meta.golden_approved ?? false);
        setSavedApproved(meta.golden_approved ?? false);
        setLoad({
          kind: "ready",
          duration: audio.duration_seconds || meta.recording.duration_seconds,
          setlistContent: meta.recording.setlist_content ?? [],
          blobUrl,
          peaks,
        });
      } catch (err) {
        if (!cancelled) {
          setLoad({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();

    return () => {
      cancelled = true;
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [recordingId]);

  // --- playhead tracking via rAF -------------------------------------------
  useEffect(() => {
    if (load.kind !== "ready") return;
    let raf = 0;
    const tick = () => {
      const a = audioRef.current;
      if (a) setPlayhead(a.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [load.kind]);

  // --- resolve song titles from the library --------------------------------
  useEffect(() => {
    if (load.kind !== "ready") return;
    let cancelled = false;
    const ids = Array.from(
      new Set(load.setlistContent.map((s) => s.song_id).filter(Boolean)),
    );
    if (ids.length === 0) return;
    (async () => {
      const entries = await Promise.all(
        ids.map(async (id): Promise<[string, SongRow] | null> => {
          try {
            const song = await getSong(id);
            return song ? [id, song] : null;
          } catch {
            return null; // library unavailable (browser dev) — fall back to id
          }
        }),
      );
      if (cancelled) return;
      const map: Record<string, SongRow> = {};
      for (const e of entries) if (e) map[e[0]] = e[1];
      setSongs(map);
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const songTitles = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [id, song] of Object.entries(songs)) map[id] = song.title;
    return map;
  }, [songs]);

  const togglePlay = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) void a.play();
    else a.pause();
  }, []);

  // --- zoom -----------------------------------------------------------------

  const zoomBy = useCallback(
    (factor: number) => {
      setZoom((z) => clampZoom(z * factor));
      setViewCenter(playhead); // zoom about the playhead, not the window centre
    },
    [playhead],
  );

  const resetZoom = useCallback(() => {
    setZoom(MIN_ZOOM);
    setViewCenter(playhead);
  }, [playhead]);

  // Keep the playhead on screen. Zoomed in, playback would otherwise run off
  // the right edge within seconds; this pages the window along when it nears
  // a boundary (the margin stops it re-centring every frame at the edge).
  useEffect(() => {
    if (zoom === MIN_ZOOM || view.span <= 0) return;
    if (isOutsideView(playhead, view, view.span * 0.05)) setViewCenter(playhead);
  }, [playhead, view, zoom]);

  const seekTo = useCallback(
    (t: number) => {
      const a = audioRef.current;
      const clamped = clampTime(t, duration);
      if (a) a.currentTime = clamped;
      setPlayhead(clamped);
    },
    [duration],
  );

  // --- hold-to-scrub --------------------------------------------------------

  /** Play state to restore when a hold ends, plus the reverse-scrub loop. */
  const scrubRef = useRef<{ wasPlaying: boolean; raf: number } | null>(null);

  /**
   * Run at `rate` for as long as the button is held.
   *
   * Forward uses the element's own `playbackRate`, so you still hear the audio
   * (chipmunked) and can tell where you are. Reverse can't: media elements
   * won't play backwards, so it steps `currentTime` on a rAF loop with the
   * audio paused — silent, which is what every editor does for reverse scrub.
   */
  const startScrub = useCallback(
    (rate: number) => {
      const a = audioRef.current;
      if (!a || scrubRef.current) return;
      const wasPlaying = !a.paused;
      scrubRef.current = { wasPlaying, raf: 0 };

      if (rate > 0) {
        a.playbackRate = rate;
        void a.play();
        return;
      }

      a.pause();
      let last = performance.now();
      const step = (now: number) => {
        const dt = (now - last) / 1000;
        last = now;
        const next = clampTime(a.currentTime + rate * dt, duration);
        a.currentTime = next;
        setPlayhead(next);
        if (next <= 0) return; // hit the start — stop stepping
        if (scrubRef.current) {
          scrubRef.current.raf = requestAnimationFrame(step);
        }
      };
      scrubRef.current.raf = requestAnimationFrame(step);
    },
    [duration],
  );

  const stopScrub = useCallback(() => {
    const state = scrubRef.current;
    if (!state) return;
    scrubRef.current = null;
    if (state.raf) cancelAnimationFrame(state.raf);
    const a = audioRef.current;
    if (!a) return;
    a.playbackRate = 1;
    if (state.wasPlaying) void a.play();
    else a.pause();
  }, []);

  // A hold released outside the button (or while the tab hides) must still
  // end — pointerup only fires on the element that captured it.
  useEffect(() => {
    const end = () => stopScrub();
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("blur", end);
    return () => {
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("blur", end);
    };
  }, [stopScrub]);

  // --- timeline geometry / pointer handling --------------------------------
  const fractionAtClientX = useCallback((clientX: number): number => {
    const el = timelineRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return 0;
    return Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  }, []);

  const onTimelinePointerDown = useCallback(
    (e: React.PointerEvent) => {
      // Click on empty timeline = seek. (Marker drags stop propagation.)
      if (duration <= 0) return;
      seekTo(viewFractionToTime(fractionAtClientX(e.clientX), view));
      setSelectedId(null);
    },
    [duration, fractionAtClientX, seekTo, view],
  );

  const onMarkerPointerDown = useCallback(
    (e: React.PointerEvent, id: string) => {
      e.stopPropagation();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      dragIdRef.current = id;
      setSelectedId(id);
    },
    [],
  );

  const onMarkerPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (dragIdRef.current === null || duration <= 0) return;
      const t = viewFractionToTime(fractionAtClientX(e.clientX), view);
      setMarkers((prev) => retimeMarker(prev, dragIdRef.current!, t, duration));
    },
    [duration, fractionAtClientX, view],
  );

  const onMarkerPointerUp = useCallback((e: React.PointerEvent) => {
    if (dragIdRef.current === null) return;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    dragIdRef.current = null;
  }, []);

  // --- edit actions ---------------------------------------------------------
  const setlistContent =
    load.kind === "ready" ? load.setlistContent : [];

  // --- what the audience would be seeing right now --------------------------

  /**
   * item_idx -> slides with real, cased, line-broken lyrics.
   *
   * The recording snapshot only keeps tokenized words (lowercased, stripped),
   * which is what the matcher needs but reads poorly on a slide. Re-resolving
   * from the library through the same expander the live presentation uses gets
   * the real text back — but only when the library still agrees slide-for-slide
   * with the snapshot. If the song has been edited since the capture, the
   * snapshot's indices are authoritative and we fall back to its tokens rather
   * than show lyrics that belong to a different slide.
   */
  const resolvedByItem = useMemo(() => {
    const out: Record<number, ParsedSlide[]> = {};
    for (const item of setlistContent) {
      const song = songs[item.song_id];
      if (!song) continue;
      const slides = resolveSlideSequence(song.body_text, song.slide_order, null, {
        leadingBlank: true,
      });
      if (slides.length === item.slides.length) out[item.item_idx] = slides;
    }
    return out;
  }, [setlistContent, songs]);

  const activeMarker = useMemo(
    () => activeMarkerAt(markers, playhead),
    [markers, playhead],
  );
  const upcomingMarker = useMemo(
    () => nextMarkerAt(markers, playhead),
    [markers, playhead],
  );

  // Before the first cue the audience is on the song's leading blank, so an
  // absent marker means slide 0 of the first item — not "nothing".
  const previewItemIdx =
    activeMarker?.item_idx ?? setlistContent[0]?.item_idx ?? 0;
  const previewSlideIdx = activeMarker?.slide_idx ?? 0;

  const previewSlide: ParsedSlide | undefined = useMemo(() => {
    const resolved = resolvedByItem[previewItemIdx]?.[previewSlideIdx];
    if (resolved) return resolved;
    const item = setlistContent.find((s) => s.item_idx === previewItemIdx);
    const words = item?.slides?.[previewSlideIdx];
    if (!words) return undefined;
    return {
      id: `snapshot-${previewItemIdx}-${previewSlideIdx}`,
      section: { type: "other", number: 0 },
      order_in_song: previewSlideIdx,
      order_in_section: 0,
      lyrics: words.join(" "),
      words,
    };
  }, [resolvedByItem, setlistContent, previewItemIdx, previewSlideIdx]);

  const addAtPlayhead = useCallback(() => {
    // Default target: same item as the nearest preceding marker (or item 0),
    // slide 0 — the operator then picks the real slide in the panel.
    const preceding = [...markers]
      .filter((m) => m.t_seconds <= playhead)
      .sort((a, b) => b.t_seconds - a.t_seconds)[0];
    const itemIdx = preceding?.item_idx ?? setlistContent[0]?.item_idx ?? 0;
    const id = newMarkerId();
    setMarkers((prev) =>
      insertMarker(prev, {
        id,
        t_seconds: clampTime(playhead, duration),
        item_idx: itemIdx,
        slide_idx: 0,
      }),
    );
    setSelectedId(id);
  }, [markers, playhead, duration, setlistContent]);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    setMarkers((prev) => removeMarker(prev, selectedId));
    setSelectedId(null);
  }, [selectedId]);

  const selected = markers.find((m) => m.id === selectedId) ?? null;

  const retarget = useCallback(
    (itemIdx: number, slideIdx: number) => {
      if (!selectedId) return;
      setMarkers((prev) => retargetMarker(prev, selectedId, itemIdx, slideIdx));
    },
    [selectedId],
  );

  // Keyboard: space = play/pause, Delete = remove selected.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "SELECT" || target.tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
        e.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, deleteSelected, selectedId]);

  // --- save / back ----------------------------------------------------------
  const onSave = useCallback(async () => {
    setSaving(true);
    try {
      const events = toEvents(markers);
      const res = await saveGolden(recordingId, events, "", approved);
      if (res.saved) {
        setOriginal(events);
        setSavedApproved(approved);
        toast.success(
          approved ? "Saved and marked approved." : "Golden annotation saved.",
        );
      } else {
        toast.error(`Save failed: ${res.error ?? "unknown error"}`);
      }
    } catch (err) {
      toast.error(`Save failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setSaving(false);
    }
  }, [markers, recordingId, toast, approved]);

  const onBackGuarded = useCallback(async () => {
    if (dirty) {
      const ok = await confirm({
        title: "Discard unsaved changes?",
        message: "Your edits to this recording's transitions haven't been saved.",
        confirmLabel: "Discard",
        cancelLabel: "Keep editing",
        variant: "destructive",
      });
      if (!ok) return;
    }
    onBack();
  }, [dirty, confirm, onBack]);

  // --- render ---------------------------------------------------------------
  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <Button type="button" variant="ghost" size="sm" onClick={onBackGuarded}>
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">Golden recording editor</div>
          <div className="truncate text-xs text-muted-foreground">{recordingId}</div>
        </div>
        {dirty && (
          <span className="text-xs text-amber-500">Unsaved changes</span>
        )}
        <Button
          type="button"
          size="sm"
          variant={approved ? "default" : "outline"}
          onClick={() => setApproved((a) => !a)}
          disabled={load.kind !== "ready"}
          title={
            approved
              ? "Approved — click to withdraw sign-off"
              : "Mark this timeline as reviewed and correct"
          }
        >
          <Star className={`size-4 ${approved ? "fill-current" : ""}`} />
          {approved ? "Approved" : "Approve"}
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onSave}
          disabled={!dirty || saving || load.kind !== "ready"}
        >
          <Save className="size-4" />
          {saving ? "Saving…" : "Save golden"}
        </Button>
      </header>

      {load.kind === "loading" && (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Loading recording…
        </div>
      )}

      {load.kind === "error" && (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-destructive">
          {load.message}
        </div>
      )}

      {load.kind === "ready" && (
        <div className="flex flex-1 flex-col gap-4 overflow-auto p-4">
          {/* hidden audio element drives playback */}
          <audio
            ref={audioRef}
            src={load.blobUrl}
            preload="auto"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />

          {/* transport */}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" size="icon" onClick={togglePlay}>
              {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            </Button>

            <HoldButton
              rate={-4}
              label="4×"
              title="Hold to rewind at 4× (silent)"
              onStart={startScrub}
              onStop={stopScrub}
            >
              <Rewind className="size-4" />
            </HoldButton>
            <HoldButton
              rate={2}
              label="2×"
              title="Hold to play at 2×"
              onStart={startScrub}
              onStop={stopScrub}
            />
            <HoldButton
              rate={4}
              label="4×"
              title="Hold to play at 4×"
              onStart={startScrub}
              onStop={stopScrub}
            />

            <div className="ml-1 font-mono text-sm tabular-nums">
              {formatTime(playhead)} / {formatTime(duration)}
            </div>

            <div className="flex-1" />

            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => zoomBy(1 / ZOOM_STEP)}
                disabled={zoom <= MIN_ZOOM}
                title="Zoom out"
              >
                <ZoomOut className="size-4" />
              </Button>
              <button
                type="button"
                onClick={resetZoom}
                disabled={zoom <= MIN_ZOOM}
                title="Fit whole recording"
                className="min-w-12 rounded px-1 font-mono text-xs tabular-nums text-muted-foreground hover:text-foreground disabled:hover:text-muted-foreground"
              >
                {zoom.toFixed(0)}×
              </button>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => zoomBy(ZOOM_STEP)}
                disabled={zoom >= MAX_ZOOM}
                title="Zoom in (centres on the playhead)"
              >
                <ZoomIn className="size-4" />
              </Button>
            </div>

            <Button type="button" variant="secondary" size="sm" onClick={addAtPlayhead}>
              <Plus className="size-4" />
              Add transition at playhead
            </Button>
          </div>

          {/* live slide preview — what the audience would see at the playhead */}
          <SlidePreview
            slide={previewSlide}
            itemIdx={previewItemIdx}
            slideIdx={previewSlideIdx}
            slideCount={
              setlistContent.find((s) => s.item_idx === previewItemIdx)?.slides
                .length ?? 0
            }
            songTitle={
              songTitles[
                setlistContent.find((s) => s.item_idx === previewItemIdx)
                  ?.song_id ?? ""
              ]
            }
            activeMarker={activeMarker}
            upcomingMarker={upcomingMarker}
            playhead={playhead}
            multiItem={setlistContent.length > 1}
            onSelectActive={() =>
              activeMarker && setSelectedId(activeMarker.id)
            }
          />

          {/* timeline + waveform */}
          <div
            ref={timelineRef}
            className="relative h-32 w-full cursor-pointer select-none rounded-md border border-border bg-muted/30"
            onPointerDown={onTimelinePointerDown}
            onPointerMove={onMarkerPointerMove}
            onPointerUp={onMarkerPointerUp}
          >
            <WaveformCanvas
              peaks={load.peaks}
              startFrac={duration > 0 ? view.start / duration : 0}
              endFrac={duration > 0 ? view.end / duration : 1}
            />

            {/* transition markers */}
            {markers.map((m) => {
              const frac = timeToViewFraction(m.t_seconds, view);
              if (frac < -0.02 || frac > 1.02) return null; // off-screen
              const left = `${frac * 100}%`;
              const isSel = m.id === selectedId;
              // The marker currently driving the preview — tying the two
              // together is what lets you see which cue you're auditioning.
              const isActive = !isSel && activeMarker?.id === m.id;
              const tone = isSel
                ? "bg-amber-400"
                : isActive
                  ? "bg-emerald-400"
                  : "bg-indigo-400";
              const label = slideLabel(setlistContent, m.item_idx, m.slide_idx);
              return (
                <div
                  key={m.id}
                  className="absolute top-0 bottom-0 z-10 -translate-x-1/2"
                  style={{ left }}
                  onPointerDown={(e) => onMarkerPointerDown(e, m.id)}
                >
                  <div className={`h-full w-0.5 ${tone}`} />
                  <div
                    className={`absolute -top-0.5 left-1/2 size-3 -translate-x-1/2 cursor-ew-resize rounded-sm ${tone}`}
                    title={`${label.title} @ ${formatTime(m.t_seconds)}`}
                  />
                  <div
                    className={`absolute top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded px-1 text-[10px] ${
                      isSel
                        ? "bg-amber-400 text-black"
                        : isActive
                          ? "bg-emerald-400 text-black"
                          : "bg-indigo-400/80 text-white"
                    }`}
                  >
                    {m.item_idx + 1}·{m.slide_idx + 1}
                  </div>
                </div>
              );
            })}

            {/* playhead */}
            <div
              className="pointer-events-none absolute top-0 bottom-0 z-20 w-px bg-foreground"
              style={{ left: `${timeToViewFraction(playhead, view) * 100}%` }}
            />
          </div>

          {/* selected-transition editor */}
          <SelectedPanel
            selected={selected}
            setlistContent={setlistContent}
            songTitles={songTitles}
            onRetarget={retarget}
            onDelete={deleteSelected}
            onSeek={() => selected && seekTo(selected.t_seconds)}
          />
        </div>
      )}
    </div>
  );
}

/**
 * A transport button that runs only while held.
 *
 * Hold-to-activate rather than toggle: reviewing markers is a two-handed,
 * momentary job — you skim to the next cue and let go, and there's no speed
 * left switched on to catch you out on the following one.
 */
function HoldButton({
  rate,
  label,
  title,
  onStart,
  onStop,
  children,
}: {
  rate: number;
  label: string;
  title: string;
  onStart: (rate: number) => void;
  onStop: () => void;
  children?: React.ReactNode;
}) {
  const [held, setHeld] = useState(false);
  return (
    <Button
      type="button"
      variant={held ? "default" : "outline"}
      size="sm"
      title={title}
      aria-label={title}
      className="gap-1 select-none"
      onPointerDown={(e) => {
        e.preventDefault(); // don't steal focus / start a text selection
        setHeld(true);
        onStart(rate);
      }}
      onPointerUp={() => {
        setHeld(false);
        onStop();
      }}
      onPointerLeave={() => {
        if (!held) return;
        setHeld(false);
        onStop();
      }}
      onPointerCancel={() => {
        setHeld(false);
        onStop();
      }}
    >
      {children}
      <span className="font-mono text-xs tabular-nums">{label}</span>
    </Button>
  );
}

/** Nominal projector size the miniature is scaled down from. */
const STAGE_W = 1280;
const STAGE_H = 720;

/**
 * `SlideStage` at projector size, scaled down to fit the panel.
 *
 * The stage sizes its type in `vw`, so dropping it into a 420px box would
 * render viewport-sized text in a thumbnail and clip it. Rendering at a fixed
 * 1280×720 and transform-scaling the whole thing keeps the proportions honest:
 * if a line overflows here, it overflows on the real projector too.
 */
function ScaledStage({ slide }: { slide: ParsedSlide | undefined }) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const apply = () => setScale(el.clientWidth / STAGE_W);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={boxRef}
      className="relative aspect-video w-full overflow-hidden rounded-md border border-border bg-black"
    >
      <div
        className="absolute top-0 left-0 origin-top-left"
        style={{
          width: STAGE_W,
          height: STAGE_H,
          transform: `scale(${scale})`,
          // Hide the unscaled flash on first paint, before the observer runs.
          visibility: scale > 0 ? "visible" : "hidden",
        }}
      >
        <SlideStage slide={slide} blankMode="none" />
      </div>
    </div>
  );
}

/**
 * The slide that would be on the screen at the playhead, rendered through the
 * same `SlideStage` the audience window uses.
 *
 * This is the thing that makes a marker assessable: a timestamp in a list says
 * nothing about whether it's right, but a slide that flips to the next line
 * exactly as the band sings it is self-evidently correct — or obviously early.
 */
function SlidePreview({
  slide,
  itemIdx,
  slideIdx,
  slideCount,
  songTitle,
  activeMarker,
  upcomingMarker,
  playhead,
  multiItem,
  onSelectActive,
}: {
  slide: ParsedSlide | undefined;
  itemIdx: number;
  slideIdx: number;
  slideCount: number;
  songTitle: string | undefined;
  activeMarker: TimelineMarker | null;
  upcomingMarker: TimelineMarker | null;
  playhead: number;
  multiItem: boolean;
  onSelectActive: () => void;
}) {
  const untilNext = upcomingMarker
    ? upcomingMarker.t_seconds - playhead
    : null;

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
      <div className="w-full sm:w-[420px] sm:shrink-0">
        <ScaledStage slide={slide} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5 rounded-md border border-border px-4 py-3">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          On screen at playhead
        </div>
        <div className="truncate text-sm font-medium">
          {songTitle ?? (multiItem ? `Item ${itemIdx + 1}` : "—")}
        </div>
        <div className="text-sm text-muted-foreground">
          Slide {slideIdx + 1}
          {slideCount > 0 && ` of ${slideCount}`}
          {slide && slide.words.length === 0 && " · blank"}
        </div>

        {activeMarker ? (
          <button
            type="button"
            onClick={onSelectActive}
            className="self-start text-xs text-indigo-400 underline-offset-2 hover:underline"
          >
            from transition @ {formatTime(activeMarker.t_seconds)} — select it
          </button>
        ) : (
          <div className="text-xs text-muted-foreground">
            before the first transition
          </div>
        )}

        <div className="text-xs text-muted-foreground">
          {untilNext === null
            ? "no further transitions"
            : `next in ${untilNext.toFixed(1)}s`}
        </div>
      </div>
    </div>
  );
}

function SelectedPanel({
  selected,
  setlistContent,
  songTitles,
  onRetarget,
  onDelete,
  onSeek,
}: {
  selected: TimelineMarker | null;
  setlistContent: SetlistItemSnapshot[];
  songTitles: Record<string, string>;
  onRetarget: (itemIdx: number, slideIdx: number) => void;
  onDelete: () => void;
  onSeek: () => void;
}) {
  if (!selected) {
    return (
      <div className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
        Select a transition marker to retarget or delete it, or drag a marker to
        retime it. Use “Add transition at playhead” to insert a new one.
      </div>
    );
  }

  const item = setlistContent.find((s) => s.item_idx === selected.item_idx);
  const slideCount = item?.slides.length ?? 0;
  const label = slideLabel(setlistContent, selected.item_idx, selected.slide_idx);

  return (
    <div className="rounded-md border border-border p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-medium">
          Transition @ {formatTime(selected.t_seconds)}
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onSeek}>
            Seek here
          </Button>
          <Button type="button" variant="destructive" size="sm" onClick={onDelete}>
            <Trash2 className="size-4" />
            Delete
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Item</span>
          <select
            className="h-9 min-w-48 max-w-72 rounded-md border border-input bg-background px-2 text-sm"
            value={selected.item_idx}
            onChange={(e) => onRetarget(Number(e.target.value), 0)}
          >
            {setlistContent.map((s) => (
              <option key={s.item_idx} value={s.item_idx}>
                {s.item_idx + 1}. {songTitles[s.song_id] ?? s.song_id}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Slide</span>
          <select
            className="h-9 min-w-64 max-w-96 rounded-md border border-input bg-background px-2 text-sm"
            value={selected.slide_idx}
            onChange={(e) => onRetarget(selected.item_idx, Number(e.target.value))}
          >
            {Array.from({ length: slideCount }, (_, i) => i).map((i) => {
              const preview = slidePreview(item?.slides ?? [], i);
              return (
                <option key={i} value={i}>
                  {preview ? `Slide ${i + 1} — ${preview}` : `Slide ${i + 1} (blank)`}
                </option>
              );
            })}
          </select>
        </label>

        <div className="min-w-0 flex-1 text-xs text-muted-foreground">
          <div className="font-medium text-foreground">{label.title}</div>
          <div className="truncate">{label.preview || "—"}</div>
        </div>
      </div>
    </div>
  );
}
