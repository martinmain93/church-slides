/**
 * Model Inspector — watch the matcher think, against the audio it heard.
 *
 * The eval harness reduces a run to a few numbers, which tell you something is
 * wrong but never why. This lays the model's per-update internal state on the
 * same timeline as the waveform and the human-approved ground truth:
 *
 *   - waveform in the middle, scrubbable, with a playhead
 *   - **below**: golden markers — what a human said the slides were
 *   - **above**: model markers — what the matcher committed to
 *   - side panel: every internal parameter at the playhead
 *
 * Vertical alignment is the whole point. A model marker sitting to the right of
 * its golden counterpart is a late commit; one with no counterpart is a wrong
 * one; a stretch of golden markers with nothing above them is the tracker
 * having lost the song.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ArrowLeft, Pause, Play, RefreshCw, ZoomIn, ZoomOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/lib/toast";
import {
  inspectLookback,
  inspectMusic,
  loadRecording,
  loadRecordingAudio,
  replayRecording,
  type LookbackSource,
} from "@/lib/recordingClient";
import { LookbackPassLane, LookbackStatePanel } from "@/components/recordings/LookbackLayers";
import { MusicLayers, useFollowedTime } from "@/components/recordings/MusicLayers";
import {
  attachBandSolo,
  followOutputClock,
  type BandSolo,
  type PlayAnchor,
} from "@/lib/recordings/bandSolo";
import {
  clampTime,
  clampZoom,
  formatTime,
  isOutsideView,
  MAX_ZOOM,
  MIN_ZOOM,
  timeToViewFraction,
  viewFractionToTime,
  viewWindow,
  ZOOM_STEP,
} from "@/lib/recordings/timeline";
import type {
  GroundTruthEvent,
  SetlistItemSnapshot,
  InspectMusicResponse,
  LookbackItemView,
  TraceDecisionMsg,
  TraceSampleMsg,
} from "@/types/generated";
import { computePeaks, WaveformCanvas, type Peaks } from "./WaveformCanvas";

interface Props {
  recordingId: string;
  onBack: () => void;
}

type LoadState =
  | { kind: "loading"; step: string }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      duration: number;
      blobUrl: string;
      peaks: Peaks | null;
      golden: GroundTruthEvent[];
      setlistContent: SetlistItemSnapshot[];
    };

function playUrl(el: HTMLAudioElement, url: string) {
  if (el.src === url) return;
  const t = el.currentTime;
  const playing = !el.paused;
  el.addEventListener(
    "loadedmetadata",
    () => {
      const dur = Number.isFinite(el.duration) ? el.duration : t;
      el.currentTime = Math.min(t, dur);
      if (playing) void el.play();
    },
    { once: true },
  );
  el.src = url;
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const PlayheadRule = memo(function PlayheadRule({
  ruleRef,
}: {
  ruleRef: RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={ruleRef}
      className="pointer-events-none absolute inset-y-0 z-20 w-px bg-foreground"
    />
  );
});

const PlayClock = memo(function PlayClock({
  clockRef,
}: {
  clockRef: RefObject<HTMLDivElement | null>;
}) {
  return <div ref={clockRef} className="font-mono text-sm tabular-nums" />;
});

/** The last trace sample at or before `t` — what the model knew then. */
export function sampleAt(
  samples: TraceSampleMsg[],
  t: number,
): TraceSampleMsg | null {
  let best: TraceSampleMsg | null = null;
  for (const s of samples) {
    if (s.t_seconds <= t) best = s;
    else break;
  }
  return best;
}

export function ModelInspector({ recordingId, onBack }: Props) {
  const toast = useToast();
  const [load, setLoad] = useState<LoadState>({
    kind: "loading",
    step: "Loading recording…",
  });
  const [samples, setSamples] = useState<TraceSampleMsg[]>([]);
  const [decisions, setDecisions] = useState<TraceDecisionMsg[]>([]);
  const [notes, setNotes] = useState("");
  const [replaying, setReplaying] = useState(false);
  const [tracker, setTracker] = useState<"forward" | "lookback">("forward");
  const [wordsSource, setWordsSource] = useState<LookbackSource>("auto");
  const [lookback, setLookback] = useState<{
    items: LookbackItemView[];
    source: string;
    sources: string[];
    goldenKind: string;
  } | null>(null);
  const [music, setMusic] = useState<InspectMusicResponse | null>(null);
  const [musicStatus, setMusicStatus] = useState<"loading" | "ready" | "error">("loading");
  const [musicError, setMusicError] = useState<string | undefined>();
  const [soloBand, setSoloBand] = useState<string | null>(null);
  const [musicSource, setMusicSource] = useState<"mix" | "stems">("mix");
  const [stemMusic, setStemMusic] = useState<InspectMusicResponse | null>(null);
  const [stemStatus, setStemStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [stemError, setStemError] = useState<string | undefined>();
  const stemUrls = useRef<Record<string, string>>({});
  const stemFor = useRef("");

  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [viewCenter, setViewCenter] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const soloRef = useRef<BandSolo | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const duration = load.kind === "ready" ? load.duration : 0;
  const view = useMemo(
    () => viewWindow(duration, zoom, viewCenter),
    [duration, zoom, viewCenter],
  );
  const waveRuleRef = useRef<HTMLDivElement | null>(null);
  const layerRuleRef = useRef<HTMLDivElement | null>(null);
  const clockRef = useRef<HTMLDivElement | null>(null);
  const playheadRef = useRef(0);
  const playAnchor = useRef<PlayAnchor | null>(null);
  const requestedCenter = useRef<number | null>(null);
  const paintRef = useRef<(t: number) => void>(() => {});
  const viewRef = useRef(view);
  const zoomRef = useRef(zoom);
  const durationRef = useRef(duration);
  viewRef.current = view;
  zoomRef.current = zoom;
  durationRef.current = duration;

  // --- load audio + golden ---------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoad({ kind: "loading", step: "Loading recording…" });
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
        const blob = new Blob([bytes.buffer.slice(0) as ArrayBuffer], {
          type: "audio/wav",
        });
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;

        let peaks: Peaks | null = null;
        try {
          const AC =
            window.AudioContext ||
            (window as unknown as { webkitAudioContext: typeof AudioContext })
              .webkitAudioContext;
          const ctx = new AC();
          const decoded = await ctx.decodeAudioData(
            bytes.buffer.slice(0) as ArrayBuffer,
          );
          peaks = computePeaks(decoded.getChannelData(0));
          void ctx.close();
        } catch {
          peaks = null;
        }
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        setLoad({
          kind: "ready",
          duration: audio.duration_seconds || meta.recording.duration_seconds,
          blobUrl,
          peaks,
          golden: meta.golden_ground_truth ?? meta.recording.ground_truth,
          setlistContent: meta.recording.setlist_content ?? [],
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

  const runReplay = useCallback(async () => {
    setReplaying(true);
    try {
      if (tracker === "lookback") {
        // the other tracker's markers must not sit on the timeline meanwhile
        setSamples([]);
        setDecisions([]);
        const reply = await inspectLookback(recordingId, wordsSource);
        if (reply.error) {
          toast.error(`Look-back failed: ${reply.error}`);
          return;
        }
        setSamples([]);
        setDecisions([]);
        setLookback({
          items: reply.items ?? [],
          source: reply.source ?? "",
          sources: reply.sources_available ?? [],
          goldenKind: reply.golden_kind ?? "shown",
        });
        setNotes(`look-back over ${reply.source} words`);
        return;
      }
      setLookback(null);
      const reply = await replayRecording(recordingId);
      if (reply.error) {
        toast.error(`Replay failed: ${reply.error}`);
        return;
      }
      setSamples(reply.samples ?? []);
      setDecisions(reply.decisions ?? []);
      setNotes(reply.notes ?? "");
    } catch (err) {
      toast.error(`Replay failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setReplaying(false);
    }
  }, [recordingId, tracker, wordsSource, toast]);

  useEffect(() => {
    if (load.kind !== "ready") return;
    let cancelled = false;
    setMusic(null);
    setMusicStatus("loading");
    setMusicError(undefined);
    inspectMusic(recordingId, (partial) => {
      if (cancelled || partial.error) return;
      setMusic(partial);
      setMusicStatus("loading");
    })
      .then((reply) => {
        if (cancelled) return;
        if (reply.error) {
          setMusicStatus("error");
          setMusicError(reply.error);
          return;
        }
        setMusic(reply);
        setMusicStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setMusicStatus("error");
        setMusicError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [load.kind, recordingId]);

  useEffect(() => {
    setMusicSource("mix");
    setStemMusic(null);
    setStemStatus("idle");
    setStemError(undefined);
    setSoloBand(null);
    stemFor.current = "";
    const urls = stemUrls.current;
    stemUrls.current = {};
    for (const url of Object.values(urls)) URL.revokeObjectURL(url);
  }, [recordingId]);

  const chooseMusic = (next: "mix" | "stems") => {
    setSoloBand(null);
    setMusicSource(next);
    if (next !== "stems") return;
    if (stemFor.current === recordingId && (stemStatus === "loading" || stemStatus === "ready")) {
      return;
    }
    const requested = recordingId;
    stemFor.current = requested;
    setStemStatus("loading");
    setStemError(undefined);
    setStemMusic(null);
    inspectMusic(
      requested,
      (partial) => {
        if (stemFor.current !== requested || partial.error) return;
        setStemMusic(partial);
      },
      "stems",
    )
      .then((reply) => {
        if (stemFor.current !== requested) return;
        if (reply.error) {
          setStemStatus("error");
          setStemError(reply.error);
          return;
        }
        setStemMusic(reply);
        setStemStatus("ready");
      })
      .catch((err: unknown) => {
        if (stemFor.current !== requested) return;
        setStemStatus("error");
        setStemError(err instanceof Error ? err.message : String(err));
      });
  };

  // Replay once the recording is loaded, and again whenever the engine choice
  // changes — comparing the two trackers on one timeline is the point.
  useEffect(() => {
    if (load.kind === "ready") void runReplay();
  }, [load.kind, tracker, wordsSource]); // eslint-disable-line react-hooks/exhaustive-deps

  // The rule and the clock are painted every frame from the audio clock.
  // Nothing here writes React state on a timer: a per-frame setState re-rendered
  // the waveform, the music plot, and the side panel, and that stall left the
  // cursor a bar behind the speakers until pause. The view recenters only when
  // the line walks out of a zoomed window.
  useEffect(() => {
    if (load.kind !== "ready") return;
    let raf = 0;
    const paint = (t: number) => {
      playheadRef.current = t;
      const v = viewRef.current;
      const left = `${timeToViewFraction(t, v) * 100}%`;
      if (waveRuleRef.current) waveRuleRef.current.style.left = left;
      if (layerRuleRef.current) layerRuleRef.current.style.left = left;
      if (clockRef.current) {
        clockRef.current.textContent = `${formatTime(t)} / ${formatTime(durationRef.current)}`;
      }
      const z = zoomRef.current;
      if (z === MIN_ZOOM || v.span <= 0 || !isOutsideView(t, v, v.span * 0.05)) {
        requestedCenter.current = null;
      } else {
        const center = (v.start + v.end) / 2;
        const arrived =
          requestedCenter.current != null && Math.abs(center - requestedCenter.current) <= 0.05;
        const moved =
          requestedCenter.current == null || Math.abs(t - requestedCenter.current) > 0.05;
        // One recenter per edge crossing. A slow paint can land past that
        // window; once it has committed, ask again for where the audio is now.
        if (moved && (requestedCenter.current == null || arrived)) {
          requestedCenter.current = t;
          setViewCenter(t);
        }
      }
    };
    paintRef.current = paint;
    const tick = () => {
      const a = audioRef.current;
      if (a) {
        let t = a.currentTime;
        const solo = soloRef.current;
        if (!a.paused && solo) {
          const step = followOutputClock(t, solo.outputTime(), playAnchor.current);
          t = step.time;
          playAnchor.current = step.anchor;
        } else {
          playAnchor.current = null;
        }
        paint(t);
      }
      raf = requestAnimationFrame(tick);
    };
    const a0 = audioRef.current;
    if (a0) paint(a0.currentTime);
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      paintRef.current = () => {};
    };
  }, [load.kind]);

  useEffect(() => {
    if (load.kind !== "ready") return;
    const el = audioRef.current;
    if (!el) return;
    // Strict mode runs this effect twice on the same <audio>. A media element
    // can only be wired into Web Audio once, so the second pass reuses the
    // graph and the deferred cleanup from the first pass sees that and leaves
    // it open.
    const token = Symbol();
    const node = el as HTMLAudioElement & { __bandSolo?: BandSolo; __bandToken?: symbol };
    node.__bandToken = token;
    if (!node.__bandSolo) node.__bandSolo = attachBandSolo(el);
    soloRef.current = node.__bandSolo;
    return () => {
      const graph = node.__bandSolo;
      queueMicrotask(() => {
        if (node.__bandToken !== token || !graph) return;
        graph.close();
        node.__bandSolo = undefined;
        if (soloRef.current === graph) soloRef.current = null;
      });
    };
  }, [load]);

  const soloHz =
    musicSource === "mix" && soloBand
      ? music?.onsets?.find((row) => row.band === soloBand)
      : undefined;

  useEffect(() => {
    const graph = soloRef.current;
    const el = audioRef.current;
    if (!graph || !el || load.kind !== "ready") return;
    const mixUrl = load.blobUrl;
    let cancelled = false;

    if (musicSource !== "stems" || !soloBand) {
      if (soloHz && soloHz.hz_hi > soloHz.hz_lo) graph.setBand(soloHz.hz_lo, soloHz.hz_hi);
      else graph.clear();
      playUrl(el, mixUrl);
      return;
    }

    graph.clear();
    const stem = soloBand;
    (async () => {
      let url = stemUrls.current[stem];
      if (!url) {
        const audio = await loadRecordingAudio(recordingId, stem);
        if (cancelled) return;
        if (audio.error || !audio.audio_base64) {
          toast.error(
            audio.error?.includes("no audio")
              ? "That stem is still being written. Solo it again once separation finishes."
              : (audio.error ?? "That stem is not ready yet"),
          );
          if (!cancelled) {
            playUrl(el, mixUrl);
            setSoloBand(null);
          }
          return;
        }
        const bytes = base64ToBytes(audio.audio_base64);
        url = URL.createObjectURL(
          new Blob([bytes.buffer.slice(0) as ArrayBuffer], { type: "audio/wav" }),
        );
        stemUrls.current[stem] = url;
      }
      if (cancelled) return;
      playUrl(el, url);
    })();
    return () => {
      cancelled = true;
    };
  }, [soloBand, musicSource, soloHz, load, recordingId, toast]);

  const togglePlay = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    soloRef.current?.resume();
    if (a.paused) void a.play();
    else a.pause();
  }, []);

  const seekTo = useCallback(
    (t: number) => {
      const a = audioRef.current;
      const clamped = clampTime(t, duration);
      if (a) a.currentTime = clamped;
      playAnchor.current = null;
      paintRef.current(clamped);
      setPlayhead(clamped);
    },
    [duration],
  );

  const onTimelinePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (duration <= 0) return;
      const el = timelineRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const frac = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
      seekTo(viewFractionToTime(frac, view));
    },
    [duration, seekTo, view],
  );

  const zoomBy = useCallback(
    (factor: number) => {
      setZoom((z) => clampZoom(z * factor));
      setViewCenter(playheadRef.current);
    },
    [],
  );

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <Button type="button" variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">Model inspector</div>
          <div className="truncate text-xs text-muted-foreground">
            {recordingId}
            {notes ? ` · ${notes}` : ""}
          </div>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Music
          <select
            className="rounded border bg-background px-1.5 py-0.5 text-xs"
            value={musicSource}
            onChange={(e) => chooseMusic(e.target.value as "mix" | "stems")}
          >
            <option value="mix">Mix</option>
            <option value="stems">Stems</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Tracker
          <select
            className="rounded border bg-background px-1.5 py-0.5 text-xs"
            value={tracker}
            onChange={(e) =>
              setTracker(e.target.value as "forward" | "lookback")
            }
          >
            <option value="forward">Forward (shipping)</option>
            <option value="lookback">Look-back (learning)</option>
          </select>
        </label>
        {tracker === "lookback" && (
          <label
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
            title="Which words the look-back reads"
          >
            Words
            <select
              className="rounded border bg-background px-1.5 py-0.5 text-xs"
              value={wordsSource}
              onChange={(e) => setWordsSource(e.target.value as LookbackSource)}
            >
              <option value="auto">Best available</option>
              <option value="live">Live (as heard in the service)</option>
              <option value="asr">Whisper, streamed like live (slow first time)</option>
              <option value="cached">Whisper, whole file (cached)</option>
              <option value="golden">Perfect, from the golden (ceiling)</option>
            </select>
          </label>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={runReplay}
          disabled={replaying || load.kind !== "ready"}
        >
          <RefreshCw className={`size-4 ${replaying ? "animate-spin" : ""}`} />
          {replaying ? (tracker === "lookback" ? "Looking back…" : "Replaying…") : "Re-run"}
        </Button>
      </header>

      {load.kind === "loading" && (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {load.step}
        </div>
      )}
      {load.kind === "error" && (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-destructive">
          {load.message}
        </div>
      )}

      {load.kind === "ready" && (
        <div className="flex min-h-0 flex-1 gap-4 p-4">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex shrink-0 items-center gap-2 pb-3">
              <Button type="button" variant="outline" size="icon" onClick={togglePlay}>
                {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
              </Button>
              <PlayClock clockRef={clockRef} />
              <div className="flex-1" />
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
              <span className="min-w-10 text-center font-mono text-xs tabular-nums text-muted-foreground">
                {zoom.toFixed(0)}×
              </span>
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
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
            <audio
              ref={audioRef}
              preload="auto"
              onPlay={() => setPlaying(true)}
              onPause={() => {
                setPlaying(false);
                const a = audioRef.current;
                if (!a) return;
                playAnchor.current = null;
                paintRef.current(a.currentTime);
                setPlayhead(a.currentTime);
              }}
              onEnded={() => {
                setPlaying(false);
                const a = audioRef.current;
                if (!a) return;
                playAnchor.current = null;
                paintRef.current(a.currentTime);
                setPlayhead(a.currentTime);
              }}
            />

            <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <span
                  className={`inline-block h-2 w-2 rounded-sm ${tracker === "lookback" ? "bg-emerald-400/80" : "bg-sky-400"}`}
                />
                {tracker === "lookback"
                  ? "look-back passes (above; amber/grey = unsure, red ring = golden disagrees)"
                  : "model commits (above)"}
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-sm bg-emerald-400" />
                golden ground truth (below)
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-sm bg-amber-500/70" />
                lost
              </span>
            </div>

            {/* timeline: model markers / waveform / golden markers.
                The spacer matches the music panel's row labels so a beat
                lines up with the same instant on the waveform. */}
            <div className="flex">
            <div className="w-14 shrink-0" aria-hidden />
            <div
              ref={timelineRef}
              className="relative h-56 min-w-0 flex-1 cursor-pointer select-none rounded-md border border-border bg-muted/30"
              onPointerDown={onTimelinePointerDown}
            >
              {/* lost-mode shading, drawn first so markers sit on top */}
              {samples.map((s, i) => {
                if (!s.is_lost) return null;
                const next = samples[i + 1];
                const lo = timeToViewFraction(s.t_seconds, view);
                const hi = timeToViewFraction(
                  next ? next.t_seconds : s.t_seconds + 0.5,
                  view,
                );
                if (hi < 0 || lo > 1) return null;
                return (
                  <div
                    key={`lost-${i}`}
                    className="pointer-events-none absolute top-0 bottom-0 bg-amber-500/15"
                    style={{ left: `${lo * 100}%`, width: `${Math.max(0, hi - lo) * 100}%` }}
                  />
                );
              })}

              {/* model commits — above the waveform (or the look-back's passes) */}
              {lookback && <LookbackPassLane items={lookback.items} view={view} />}
              <div className="absolute inset-x-0 top-0 h-16">
                {decisions.map((d, i) => {
                  const frac = timeToViewFraction(d.t_seconds, view);
                  if (frac < -0.02 || frac > 1.02) return null;
                  return (
                    <div
                      key={`d-${i}`}
                      className="absolute top-0 -translate-x-1/2"
                      style={{ left: `${frac * 100}%` }}
                      title={`model → slide ${d.slide_idx + 1} @ ${formatTime(d.t_seconds)}`}
                    >
                      <div className="rounded-sm bg-sky-400 px-1 text-[10px] font-medium text-black">
                        {d.slide_idx + 1}
                      </div>
                      <div className="mx-auto h-10 w-px bg-sky-400/70" />
                    </div>
                  );
                })}
              </div>

              <div className="absolute inset-x-0 top-16 bottom-16">
                <WaveformCanvas
                  peaks={load.peaks}
                  startFrac={duration > 0 ? view.start / duration : 0}
                  endFrac={duration > 0 ? view.end / duration : 1}
                />
              </div>

              {/* golden ground truth — below the waveform */}
              <div className="absolute inset-x-0 bottom-0 h-16">
                {load.golden.map((g, i) => {
                  const frac = timeToViewFraction(g.t_seconds, view);
                  if (frac < -0.02 || frac > 1.02) return null;
                  return (
                    <div
                      key={`g-${i}`}
                      className="absolute bottom-0 -translate-x-1/2"
                      style={{ left: `${frac * 100}%` }}
                      title={`golden → slide ${g.slide_idx + 1} @ ${formatTime(g.t_seconds)}`}
                    >
                      <div className="mx-auto h-10 w-px bg-emerald-400/70" />
                      <div className="rounded-sm bg-emerald-400 px-1 text-[10px] font-medium text-black">
                        {g.slide_idx + 1}
                      </div>
                    </div>
                  );
                })}
              </div>

              <PlayheadRule ruleRef={waveRuleRef} />
            </div>
            </div>

            <MusicLayers
              data={musicSource === "stems" ? stemMusic : music}
              view={view}
              playhead={playhead}
              status={
                musicSource === "stems"
                  ? stemStatus === "idle"
                    ? "loading"
                    : stemStatus
                  : musicStatus
              }
              error={musicSource === "stems" ? stemError : musicError}
              pendingLabel={
                musicSource === "stems" ? "Separating drums, bass, other, and vocals…" : undefined
              }
              onSeek={seekTo}
              soloBand={soloBand}
              onSoloBand={setSoloBand}
              playheadRuleRef={layerRuleRef}
              playheadTimeRef={playheadRef}
            />
          </div>
          </div>

          {tracker === "lookback" ? (
            <LookbackStatePanel
              playheadRef={playheadRef}
              items={lookback?.items ?? []}
              golden={load.golden}
              setlistContent={load.setlistContent}
              source={lookback?.source ?? ""}
              sourcesAvailable={lookback?.sources ?? []}
              goldenKind={lookback?.goldenKind ?? "shown"}
            />
          ) : (
            <LiveStatePanel
              playheadRef={playheadRef}
              samples={samples}
              golden={load.golden}
              setlistContent={load.setlistContent}
            />
          )}
        </div>
      )}
    </div>
  );
}

function LiveStatePanel({
  playheadRef,
  samples,
  golden,
  setlistContent,
}: {
  playheadRef: RefObject<number>;
  samples: TraceSampleMsg[];
  golden: GroundTruthEvent[];
  setlistContent: SetlistItemSnapshot[];
}) {
  const t = useFollowedTime(playheadRef);
  const ordered = useMemo(
    () => [...golden].sort((a, b) => a.t_seconds - b.t_seconds),
    [golden],
  );
  const sample = sampleAt(samples, t);
  let goldenAt: GroundTruthEvent | null = null;
  for (const g of ordered) {
    if (g.t_seconds <= t) goldenAt = g;
    else break;
  }
  const item = setlistContent.find((s) => s.item_idx === (sample?.item_idx ?? 0));
  return (
    <StatePanel
      sample={sample}
      goldenSlide={goldenAt?.slide_idx ?? null}
      agreeing={sample && goldenAt ? sample.displayed_slide === goldenAt.slide_idx : null}
      slideWords={sample && item ? item.slides[sample.slide_idx] : undefined}
      sampleCount={samples.length}
    />
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-mono tabular-nums ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

function Bar({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="h-1 w-full overflow-hidden rounded bg-muted">
      <div
        className={`h-full ${tone}`}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

function StatePanel({
  sample,
  goldenSlide,
  agreeing,
  slideWords,
  sampleCount,
}: {
  sample: TraceSampleMsg | null;
  goldenSlide: number | null;
  agreeing: boolean | null;
  slideWords: string[] | undefined;
  sampleCount: number;
}) {
  if (!sample) {
    return (
      <div className="w-72 shrink-0 rounded-md border border-dashed border-border p-4 text-xs text-muted-foreground">
        {sampleCount === 0
          ? "No trace yet — hit Re-run."
          : "Scrub to a point after the first transcript event."}
      </div>
    );
  }
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto rounded-md border border-border p-4">
      <div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          At playhead
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums">
            {(sample.displayed_slide ?? 0) + 1}
          </span>
          <span className="text-xs text-muted-foreground">
            shown · golden {goldenSlide === null ? "—" : goldenSlide + 1}
          </span>
        </div>
        <div
          className={`mt-1 text-xs font-medium ${
            agreeing === null
              ? "text-muted-foreground"
              : agreeing
                ? "text-emerald-400"
                : "text-destructive"
          }`}
        >
          {agreeing === null ? "—" : agreeing ? "matches golden" : "wrong slide"}
        </div>
      </div>

      {sample.is_lost && (
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-400">
          Lost — the tracker isn&rsquo;t sure where the band is
        </div>
      )}

      <div className="space-y-2">
        <Row label="belief slide" value={String(sample.slide_idx + 1)} />
        <div>
          <Row label="marginal" value={sample.marginal.toFixed(3)} />
          <Bar value={sample.marginal} tone="bg-indigo-400" />
        </div>
        <div>
          <Row
            label="evidence"
            value={sample.evidence.toFixed(3)}
            tone={sample.evidence < 0.45 ? "text-amber-400" : ""}
          />
          <Bar value={sample.evidence} tone="bg-emerald-400" />
        </div>
        <Row label="ESS" value={sample.ess.toFixed(0)} />
        <div>
          <Row label="position in slide" value={sample.mode_position.toFixed(3)} />
          <Bar value={sample.mode_position} tone="bg-sky-400" />
        </div>
        <Row label="rate (words/s)" value={sample.mode_rate.toFixed(2)} />
        <Row label="t" value={`${sample.t_seconds.toFixed(2)}s`} />
      </div>

      {(sample.top_3?.length ?? 0) > 0 && (
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
            Top hypotheses
          </div>
          <div className="space-y-1">
            {(sample.top_3 ?? []).map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="w-8 font-mono tabular-nums">
                  {h.slide_idx + 1}
                </span>
                <div className="flex-1">
                  <Bar
                    value={h.score}
                    tone={i === 0 ? "bg-indigo-400" : "bg-indigo-400/40"}
                  />
                </div>
                <span className="w-10 text-right font-mono tabular-nums text-muted-foreground">
                  {h.score.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {(sample.words?.length ?? 0) > 0 && (
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
            Heard here
          </div>
          <div className="text-xs">{(sample.words ?? []).join(" ")}</div>
        </div>
      )}

      {slideWords && slideWords.length > 0 && (
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
            Belief slide text
          </div>
          <div className="text-xs text-muted-foreground">
            {slideWords.join(" ")}
          </div>
        </div>
      )}
    </div>
  );
}
