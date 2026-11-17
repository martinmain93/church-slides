/**
 * `useTranscriptStream()` — keeps a persistent WS connection open and
 * accumulates a rolling list of words as `transcript_partial` events
 * arrive from the sidecar. Also exposes the audio-session state so the
 * UI can show "starting / running / stopped / error".
 *
 * `startAudioSession()` / `stopAudioSession()` are imperative actions
 * that go through the shared connection (`getSharedConnection()`) — they're
 * plain `request()` calls; the actual transcript events come back as
 * unsolicited broadcasts subscribed via `onMessage("transcript_partial", ...)`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { loadAudioInputPrefs } from "@/lib/audioInputPrefs";
import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { type SidecarConnection } from "@/lib/ws";
import type { AudioSessionStateMessage, Word } from "@/types/generated";

export type AudioSessionState = AudioSessionStateMessage["state"];

export interface TranscriptStream {
  /** State of the underlying audio session. */
  session: AudioSessionState | "disconnected";
  /** Whether the WS connection itself is up. */
  connected: boolean;
  /** Rolling list of finalized words; partials are listed separately. */
  finalWords: Word[];
  /** Most recent partial words (revised on every new partial). */
  partialWords: Word[];
  /** Last error from the audio session, if any. */
  error: string | null;
  start(opts?: StartOptions): Promise<void>;
  stop(): Promise<void>;
}

export interface StartOptions {
  backend?: "mock" | "whisper" | "groq" | "mlx";
  modelSize?: string;
  /** Override the persisted device preference for this session. */
  deviceIndex?: number | null;
}

const MAX_FINAL_WORDS = 200;

// Words live outside the component: the debug panel unmounts a tab when you
// switch away from it, and a hook-local `useState` would come back empty and
// stay empty until the next broadcast — three to five seconds while singing.
// See `lib/liveStore`.
const finalWordsStore = createLiveStore<Word[]>([]);
const partialWordsStore = createLiveStore<Word[]>([]);

pipeFromSidecar("transcript_partial", finalWordsStore, (msg, prev) => {
  if (!msg.is_final) return prev;
  const next = prev.concat(msg.words ?? []);
  return next.length > MAX_FINAL_WORDS
    ? next.slice(next.length - MAX_FINAL_WORDS)
    : next;
});
pipeFromSidecar("transcript_partial", partialWordsStore, (msg) => {
  // A final supersedes whatever partial was on screen, so it clears it —
  // otherwise the same words show twice, once finalised and once not.
  return msg.is_final ? [] : (msg.words ?? []);
});

// The sidecar acks `start_audio_session` only after the backend is loaded —
// and a first-time Whisper start downloads the model (tens of MB to >1 GB)
// before it can ack. The default 5 s request timeout would reject that
// request spuriously mid-download. Give it plenty of headroom; the audio
// session state still flows via broadcasts regardless.
const START_TIMEOUT_MS = 10 * 60 * 1000;

export function useTranscriptStream(): TranscriptStream {
  const [connected, setConnected] = useState(false);
  const [session, setSession] = useState<AudioSessionState | "disconnected">(
    "disconnected",
  );
  const finalWords = useLiveStore(finalWordsStore);
  const partialWords = useLiveStore(partialWordsStore);
  const [error, setError] = useState<string | null>(null);
  const connRef = useRef<SidecarConnection | null>(null);

  useEffect(() => {
    let cancelled = false;
    let unsubState: (() => void) | null = null;
    let unsubStatus: (() => void) | null = null;

    (async () => {
      try {
        const conn = await getSharedConnection();
        if (cancelled) return;
        connRef.current = conn;
        setConnected(conn.status === "open");

        unsubStatus = conn.onStatus((s) => {
          setConnected(s === "open");
        });

        unsubState = conn.onMessage("audio_session_state", (msg) => {
          setSession(msg.state);
          setError(msg.error ?? null);
        });
      } catch (err) {
        if (cancelled) return;
        setConnected(false);
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
      unsubState?.();
      unsubStatus?.();
      // Shared connection — only unsubscribe, never close it.
      connRef.current = null;
    };
  }, []);

  const start = useCallback(async (opts: StartOptions = {}) => {
    const conn = connRef.current;
    if (!conn) throw new Error("sidecar not connected");
    setError(null);
    // A new session starts from a clean transcript. The stores are module
    // scoped, so this is the one place that has to clear them explicitly.
    finalWordsStore.set([]);
    partialWordsStore.set([]);
    const prefs = loadAudioInputPrefs();
    const deviceIndex =
      opts.deviceIndex !== undefined ? opts.deviceIndex : prefs.device_index;
    await conn.request(
      {
        kind: "start_audio_session",
        backend: opts.backend ?? "mock",
        model_size: opts.modelSize ?? "tiny.en",
        device_index: deviceIndex,
        // Pin by name too — defeats device-list reordering across reboots.
        device_name: prefs.device_name ?? null,
      },
      START_TIMEOUT_MS,
    );
  }, []);

  const stop = useCallback(async () => {
    const conn = connRef.current;
    if (!conn) return;
    await conn.request({ kind: "stop_audio_session" });
  }, []);

  return {
    session,
    connected,
    finalWords,
    partialWords,
    error,
    start,
    stop,
  };
}
