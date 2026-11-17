/**
 * Recording lifecycle + management over the WS.
 *
 * Three flavors of helper:
 *
 * 1. `useRecordingSession({ setlistId, enabled, record })` — long-lived hook
 *    used by the service views. Opens the live AI's audio session on mount
 *    (saving it to disk only when `record` is set), sends
 *    `record_slide_change` notifications when the slide changes, and stops
 *    the session on unmount.
 * 2. `useRecordingsList()` — fetches the list of finalized recordings
 *    for the Settings UI; auto-refreshes when a `recording_finalized`
 *    broadcast arrives.
 * 3. `deleteRecording()` / `exportRecording()` — single-shot helpers.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import type { AIMode } from "@/lib/aiPrefs";
import { loadAudioInputPrefs, resolveLiveAsr } from "@/lib/audioInputPrefs";
import { FEATURES } from "@/lib/features";
import { getAiEngineStatus, getSettings } from "@/lib/tauri";
import { DEFAULT_TUNABLES, loadTunables } from "@/lib/tunables";
import type {
  GroundTruthEvent,
  LoadRecordingAudioResponse,
  LoadRecordingResponse,
  ParticleFilterTunables,
  RecordingSummary,
  InspectLookbackResponse,
  InspectMusicResponse,
  ReplayRecordingResponse,
  SetlistContent,
} from "@/types/generated";

// Whisper's first start downloads the model before the session acks; give the
// start request generous headroom so it doesn't reject mid-download. Session
// state still flows via broadcasts regardless.
const START_TIMEOUT_MS = 10 * 60 * 1000;

// --- session start failure ------------------------------------------------

/**
 * Why the service's AI session didn't start, while it hasn't — or null.
 *
 * It used to be logged to the console only, so a microphone that couldn't be
 * opened left the pill on "AI: starting" for the whole service. The pill reads
 * this to say what went wrong and to offer a retry.
 */
export interface SessionStartFailure {
  message: string;
  retry: () => void;
  /** The AI mode the session was started in. With the AI off nothing needs
   * the microphone yet, so the pill keeps a failure quiet until the operator
   * turns the AI on. */
  aiMode: AIMode;
}

let startFailure: SessionStartFailure | null = null;
const startFailureListeners = new Set<(f: SessionStartFailure | null) => void>();

function setStartFailure(f: SessionStartFailure | null) {
  startFailure = f;
  for (const cb of startFailureListeners) cb(f);
}

export function useSessionStartFailure(): SessionStartFailure | null {
  const [failure, setFailure] = useState(startFailure);
  useEffect(() => {
    setFailure(startFailure);
    startFailureListeners.add(setFailure);
    return () => {
      startFailureListeners.delete(setFailure);
    };
  }, []);
  return failure;
}

// --- session hook -----------------------------------------------------------

interface RecordingSessionOptions {
  setlistId: string;
  enabled: boolean;
  /**
   * Also save the service's audio and slide timeline to disk. Separate from
   * `enabled`: this session is what the live AI runs on, so switching
   * recording off must not switch the AI off with it. Read at start.
   */
  record?: boolean;
  /** Pre-tokenized slides for the matcher; omit for transcript-only sessions. */
  setlistContent?: SetlistContent | null;
  /** AI mode for the matcher — read at start, and again if the engine has to
   * be restarted mid-service. Defaults to "shadow" on the sidecar. */
  aiMode?: AIMode;
  /** The slide on screen now; an engine restarted mid-service re-anchors here. */
  position?: { itemIdx: number; slideIdx: number };
  /** Initial engine tunables. */
  tunables?: ParticleFilterTunables;
}

export interface RecordingSessionApi {
  /** Reports a slide change to the active recording (no-op if no session). */
  notifySlideChange(args: {
    itemIdx: number;
    slideIdx: number;
    /**
     * What moved the screen. "voice" is distinct from "override": both are
     * human intent, but one arrived through the microphone and the other
     * through a hand on the keyboard, and telling them apart is the whole
     * question when a spoken request appears not to have been followed.
     */
    source?: "manual" | "override" | "ai" | "voice";
  }): void;
  /**
   * Re-sync the matcher's indexed setlist after the operator edits the
   * running setlist mid-service (add/remove/reorder/slide edit). The matcher
   * rebuilds its index and re-anchors to `currentItemIdx`. No-op if no
   * session is active.
   */
  updateSetlistContent(content: SetlistContent, currentItemIdx: number): void;
}

export function useRecordingSession(
  opts: RecordingSessionOptions,
): RecordingSessionApi {
  const { setlistId, enabled } = opts;
  const connRef = useRef<SidecarConnection | null>(null);
  // Latest options, for a restart after the engine crashed: the mode the AI
  // is in *now*, and where the service is *now*, not where it started.
  const optsRef = useRef(opts);
  optsRef.current = opts;
  // The newest setlist content — whichever came last of the prop and an
  // `updateSetlistContent` push (the operator view edits the set without
  // changing its prop).
  const contentRef = useRef<SetlistContent | null>(opts.setlistContent ?? null);
  useEffect(() => {
    contentRef.current = opts.setlistContent ?? null;
  }, [opts.setlistContent]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let conn: SidecarConnection | null = null;
    let unsubRestart = () => {};
    let unsubState = () => {};

    const start = async (c: SidecarConnection) => {
      const { aiMode, tunables, position, record } = optsRef.current;
      const prefs = loadAudioInputPrefs();
      const asr = await resolveLiveAsr(prefs);
      const spokenChoice = await getSettings()
        .then((s) => s.spoken_requests ?? null)
        .catch(() => null);
      if (cancelled) return;
      await c.request(
        {
          kind: "start_audio_session",
          // Real transcription — the matcher needs the actual lyrics being
          // sung, not the mock backend's canned demo words. (Audio + ground
          // truth are still captured to disk regardless of backend.)
          backend: asr.backend,
          model_size: asr.model,
          record: record ?? false,
          setlist_id: setlistId,
          setlist_content: contentRef.current,
          ai_mode: aiMode ?? "shadow",
          tunables: tunables ?? loadTunables(),
          device_index: prefs.device_index,
          device_name: prefs.device_name,
          // What's on screen now, so the AI's first move has to clear the
          // same song-change bar as any later one.
          start_item_idx: position?.itemIdx ?? null,
          start_slide_idx: position?.slideIdx ?? 0,
          // A service's saved choice, or null for the engine's default (on
          // when a hosted model's key is on file). Off where the feature is
          // hidden: nobody there can see it, or switch it off.
          spoken_instructions: FEATURES.spokenInstructions ? spokenChoice : false,
        },
        // First-time Whisper start downloads the model before the session
        // can ack; the default 5 s request timeout would reject spuriously.
        START_TIMEOUT_MS,
      );
    };

    const reportFailure = async (err: unknown) => {
      // Browser dev mode has no sidecar at all, and that is expected — but
      // this used to swallow *every* failure, including the service session
      // losing a contention race to the debug panel's transcript stream.
      // The operator's only symptom was slides that never moved, with the
      // reason sitting in a rejected promise nobody read.
      // A build or machine without the engine is expected too, and the
      // AI status pill already tells the operator why — only an engine that
      // is running and still refused the session is worth a console error.
      const message = err instanceof Error ? err.message : String(err);
      const engine = await getAiEngineStatus().catch(() => null);
      if (engine?.state === "ready" && !message.includes("sidecar unavailable")) {
        console.error("start_audio_session failed — AI tracking is off:", message);
        if (!cancelled) {
          setStartFailure({
            message,
            retry: () => void attempt(),
            aiMode: optsRef.current.aiMode ?? "shadow",
          });
        }
      }
    };

    const attempt = async () => {
      setStartFailure(null);
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        connRef.current = conn;
        await start(conn);
      } catch (err) {
        await reportFailure(err);
      }
    };

    (async () => {
      try {
        conn = await getSharedConnection();
        if (cancelled) return;
        connRef.current = conn;
        // The engine crashed and came back: it has no session. Start one
        // again, then put its tracker on the slide that's up — reported as a
        // manual move, which re-anchors it exactly like an operator's click.
        const c = conn;
        // The microphone opens on the engine's capture thread, after the
        // session is built. The engine now waits for that before replying,
        // but a slow device can still outlast the wait — and a device that
        // goes away mid-service ends the session the same way. Either way
        // the only word is this broadcast, which used to reach the debug
        // panel alone, leaving the pill on "AI: starting".
        unsubState = c.onMessage("audio_session_state", (msg) => {
          // Only this service's session: the debug panel's transcript-only
          // stream broadcasts too (with no setlist), and its mic failing is
          // not the service's AI failing.
          if (cancelled || msg.setlist_id !== setlistId) return;
          if (msg.state === "error") {
            setStartFailure({
              message: msg.error || "the audio session stopped with an error",
              retry: () => void attempt(),
              aiMode: optsRef.current.aiMode ?? "shadow",
            });
          } else if (msg.state === "running") {
            setStartFailure(null);
          }
        });
        unsubRestart = c.onEngineRestart(() => {
          void start(c)
            .then(() => {
              if (cancelled) return;
              setStartFailure(null);
              const pos = optsRef.current.position;
              if (pos) {
                c.notify({
                  kind: "record_slide_change",
                  item_idx: pos.itemIdx,
                  slide_idx: pos.slideIdx,
                  source: "manual",
                });
              }
            })
            .catch(reportFailure);
        });
        await start(conn);
      } catch (err) {
        await reportFailure(err);
      }
    })();

    return () => {
      cancelled = true;
      setStartFailure(null);
      unsubRestart();
      unsubState();
      const c = connRef.current;
      connRef.current = null;
      if (c) {
        // Fire-and-forget — the unmount path doesn't need to wait. Shared
        // connection: stop the session but DON'T close the socket (other
        // hooks share it). Because every message now flows over one socket,
        // a StrictMode start→stop→start remount is applied in order.
        c.request({ kind: "stop_audio_session" }).catch(() => undefined);
      }
    };
  }, [enabled, setlistId]);

  const notifySlideChange = useCallback<RecordingSessionApi["notifySlideChange"]>(
    ({ itemIdx, slideIdx, source = "manual" }) => {
      const conn = connRef.current;
      if (!conn) return;
      // A notification: the sidecar records it and returns without replying.
      // This used to go through `request()`, which meant every slide change
      // parked a pending entry for the full 5 s timeout before rejecting into
      // the `.catch()` below. `ResponseForKind` now types it `never`, so that
      // no longer compiles.
      conn.notify({
        kind: "record_slide_change",
        item_idx: itemIdx,
        slide_idx: slideIdx,
        source,
      });
    },
    [],
  );

  const updateSetlistContent = useCallback<
    RecordingSessionApi["updateSetlistContent"]
  >((content, currentItemIdx) => {
    contentRef.current = content;
    const conn = connRef.current;
    if (!conn) return;
    void conn
      .request({
        kind: "update_setlist_content",
        setlist_content: content,
        current_item_idx: currentItemIdx,
      })
      .catch(() => undefined);
  }, []);

  // Stable identity: callers put this in hook deps (OperatorView's mutation
  // callbacks do), and a fresh object per render defeated every memoised
  // song block, re-rendering the whole slide grid on each slide change.
  return useMemo(
    () => ({ notifySlideChange, updateSetlistContent }),
    [notifySlideChange, updateSetlistContent],
  );
}

// --- list / delete / export -------------------------------------------------

export function useRecordingsList(reloadKey: number = 0): {
  state:
    | { kind: "loading" }
    | { kind: "ready"; recordings: RecordingSummary[] }
    | { kind: "error"; message: string };
  refresh: () => void;
} {
  const [bump, setBump] = useState(0);
  const [state, setState] = useState<{
    kind: "loading" | "ready" | "error";
    recordings?: RecordingSummary[];
    message?: string;
  }>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    requestSidecar({ kind: "list_recordings" })
      .then((reply) => {
        if (!cancelled) {
          setState({ kind: "ready", recordings: reply.recordings });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, bump]);

  // Auto-refresh when a recording wraps up elsewhere (e.g. a service ended
  // in another window) rather than only on mount / manual refresh.
  useEffect(() => {
    let cancelled = false;
    let unsub: (() => void) | null = null;
    getSharedConnection()
      .then((conn) => {
        if (cancelled) return;
        unsub = conn.onMessage("recording_finalized", () => {
          setBump((b) => b + 1);
        });
      })
      .catch(() => {
        // sidecar offline — nothing to subscribe to; the mount-time fetch
        // above already surfaced its own error state.
      });
    return () => {
      cancelled = true;
      unsub?.();
    };
  }, []);

  return {
    state: state as ReturnType<typeof useRecordingsList>["state"],
    refresh: () => setBump((b) => b + 1),
  };
}

export async function deleteRecording(recordingId: string): Promise<boolean> {
  const reply = await requestSidecar({
    kind: "delete_recording",
    recording_id: recordingId,
  });
  return reply.deleted;
}

export async function exportRecording(
  recordingId: string,
): Promise<{ zipPath?: string; error?: string }> {
  const reply = await requestSidecar({
    kind: "export_recording",
    recording_id: recordingId,
  });
  return { zipPath: reply.zip_path ?? undefined, error: reply.error ?? undefined };
}

// --- golden recording editor ------------------------------------------------

/** Load a recording's metadata + any human-corrected golden ground truth. */
export async function loadRecording(
  recordingId: string,
): Promise<LoadRecordingResponse> {
  return requestSidecar({
    kind: "load_recording",
    recording_id: recordingId,
  });
}

/**
 * Replay a recording through the matcher and return everything it thought.
 *
 * Runs the whole track server-side (a few hundred belief updates), so it is a
 * request-response rather than a stream — expect a second or two.
 */
export async function replayRecording(
  recordingId: string,
  opts: { targetMarginMs?: number } = {},
): Promise<ReplayRecordingResponse> {
  return requestSidecar(
    {
      kind: "replay_recording",
      recording_id: recordingId,
      // The engine the app runs (`decision.production.build_engine`).
      target_margin_ms: opts.targetMarginMs ?? DEFAULT_TUNABLES.target_margin_ms,
    },
    // Replaying a five-minute recording outruns the default request timeout.
    { timeoutMs: 60_000 },
  );
}

/**
 * The learning look-back over a stored recording, against its golden.
 *
 * `source` picks the words: what the live session heard, a streamed Whisper
 * pass (transcribed on first use, then cached — minutes for a long service),
 * the whole-file Whisper cache, or a perfect transcript made from the golden.
 */
export async function inspectLookback(
  recordingId: string,
  source: LookbackSource = "auto",
): Promise<InspectLookbackResponse> {
  return requestSidecar(
    { kind: "inspect_lookback", recording_id: recordingId, source },
    { timeoutMs: 15 * 60_000 },
  );
}

export type LookbackSource = "auto" | "live" | "asr" | "cached" | "golden";

/**
 * Beats, chords, pitch, pulse and multi-band onsets for the inspector.
 *
 * The first call on a recording runs Beat This and Basic Pitch, which can
 * take the better part of a minute. Later opens read the cache.
 */
export async function inspectMusic(
  recordingId: string,
  onPartial?: (msg: InspectMusicResponse) => void,
  source: "mix" | "stems" = "mix",
): Promise<InspectMusicResponse> {
  const conn = await getSharedConnection();
  // A long recording streams a picture every 30 s. The final message is what
  // this promise resolves with; the callback paints the prefixes. Stems
  // separate the mix first, then run the same detectors on drums, other, and vocals.
  return conn.request(
    { kind: "inspect_music", recording_id: recordingId, source },
    600_000,
    onPartial,
  );
}

/** Load a recording's captured WAV as base64 (decoded client-side). */
export async function loadRecordingAudio(
  recordingId: string,
  stem?: string,
): Promise<LoadRecordingAudioResponse> {
  // Audio can be several MB of base64; give it headroom over the 5 s default.
  return requestSidecar(
    { kind: "load_recording_audio", recording_id: recordingId, stem: stem ?? "" },
    { timeoutMs: 60_000 },
  );
}

/** Persist edited ground truth to the recording's sibling `golden.json`. */
export async function saveGolden(
  recordingId: string,
  groundTruth: GroundTruthEvent[],
  notes = "",
  approved = false,
): Promise<{ saved: boolean; error?: string }> {
  const reply = await requestSidecar({
    kind: "save_golden",
    recording_id: recordingId,
    ground_truth: groundTruth,
    notes,
    approved,
  });
  return { saved: reply.saved ?? false, error: reply.error ?? undefined };
}
