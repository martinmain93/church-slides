/**
 * The song editor's Voice button: record, send each finished sentence to the
 * chord model, and hand back the edited song.
 *
 * The microphone opens only when the person reaches for the button
 * (`warm`, on pointer-down) — never just because a song was opened — and is
 * released when the editor closes.
 *
 * A take is computed from the song as it was when the sentence was sent. If
 * the song changed while the model was working (someone kept typing), the
 * result would overwrite that work, so it is refused instead and the person
 * is asked to say it again.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ChordChart } from "@/lib/chords/chart";
import {
  releaseVoiceInput,
  requestVoiceChart,
  startVoiceRecording,
  warmVoiceInput,
  type VoiceChartTurn,
} from "@/lib/chords/voiceChart";

export interface VoiceTake {
  transcript: string;
  body: string;
  chordText: string;
  chart: ChordChart | null;
}

export type VoicePhase = "idle" | "recording" | "working";

const MAX_TURNS = 8;
export const CHANGED_WHILE_CHARTING =
  "You changed the song while that was being charted, so it wasn't applied. Say it again.";

export function useVoiceChart(options: {
  /** Off: no microphone, nothing sent. */
  enabled: boolean;
  /** Let a take rename, split, move or rewrite slides too. */
  slideEdits: boolean;
  /** The song as it is right now (read from refs, so it's never stale). */
  read: () => { body: string; chart: ChordChart | null };
  apply: (take: VoiceTake) => void;
  onError: (message: string) => void;
}) {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const historyRef = useRef<VoiceChartTurn[]>([]);
  const stopRef = useRef<(() => Promise<void>) | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(
    () => () => {
      void stopRef.current?.();
      stopRef.current = null;
      releaseVoiceInput();
    },
    [],
  );

  async function onUtterance(blob: Blob, final: boolean): Promise<"applied" | "incomplete" | "error"> {
    const { read, slideEdits, apply, onError } = optionsRef.current;
    const sent = read();
    try {
      const result = await requestVoiceChart(
        sent.body,
        sent.chart,
        sent.chart?.key,
        blob,
        historyRef.current,
        final,
        slideEdits,
      );
      if ("incomplete" in result) return "incomplete";
      if ("error" in result) {
        onError(result.error);
        return "error";
      }
      const now = read();
      if (now.body !== sent.body || now.chart !== sent.chart) {
        onError(CHANGED_WHILE_CHARTING);
        return "error";
      }
      historyRef.current = [...historyRef.current, { transcript: result.transcript, applied: result.applied }].slice(
        -MAX_TURNS,
      );
      apply(result);
      return "applied";
    } catch (err) {
      onError(err instanceof Error ? err.message : "Voice charting failed");
      return "error";
    }
  }

  async function toggle(): Promise<void> {
    if (!optionsRef.current.enabled || phase === "working") return;
    if (phase === "recording" && stopRef.current) {
      const stop = stopRef.current;
      stopRef.current = null;
      setPhase("working");
      try {
        await stop();
      } catch (err) {
        optionsRef.current.onError(err instanceof Error ? err.message : "Voice charting failed");
      } finally {
        setPhase("idle");
      }
      return;
    }
    try {
      const recording = await startVoiceRecording({ onUtterance });
      stopRef.current = recording.stop;
      setPhase("recording");
    } catch {
      stopRef.current = null;
      setPhase("idle");
      optionsRef.current.onError("Microphone isn't available");
    }
  }

  /** Open the mic a moment early, on pointer-down, so the first word isn't lost. */
  const warm = useCallback(() => {
    if (optionsRef.current.enabled) warmVoiceInput();
  }, []);
  /** A different song: earlier takes no longer apply. */
  const reset = useCallback(() => {
    historyRef.current = [];
  }, []);
  /** Undo took the last take back; the next one shouldn't build on it. */
  const dropLastTurn = useCallback(() => {
    historyRef.current = historyRef.current.slice(0, -1);
  }, []);

  return { phase, toggle, warm, reset, dropLastTurn };
}
