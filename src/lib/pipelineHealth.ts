/**
 * Live pipeline-health subscription.
 *
 * The sidecar broadcasts how far behind real time the capture → ASR pipeline
 * is running, and how much audio the capture queue has had to discard. Both
 * used to be invisible: an overloaded pipeline silently dropped audio, the
 * matcher saw a transcript full of holes, and from the operator's chair that
 * is indistinguishable from the AI simply not working.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";

export interface PipelineHealth {
  lagSeconds: number;
  droppedChunks: number;
  healthy: boolean;
  /** Share of one core the sidecar used over the reporting window. */
  cpuPercent: number;
  /** Sidecar peak resident memory, MB. */
  rssMb: number;
  /** Mean wall time of one ASR pass in the window. */
  asrMs: number;
  /** Mean wall time of one matcher update in the window. */
  matcherMs: number;
  /** Median wall time of the score follower's recent span-scoring passes
   * (they share the GPU queue with ASR). 0 when it isn't running. */
  scorerMs: number;
  /** Spans the last scoring pass scored: its own song's plus the other songs'
   * openings. 0 when the score follower isn't running. */
  scorerSpans: number;
  /** The online speech service (Groq) is refusing requests: its rate limit. */
  asrRefused: boolean;
  /** Only read while the engine is behind: Low Power Mode, or a battery near
   * empty, slows it several-fold even on a fast computer (and even once
   * plugged in), so the fix is a setting or the charger, not cloud speech. */
  lowPowerMode: boolean;
  /** null when not behind, or the computer has no battery. */
  batteryPercent: number | null;
  onBattery: boolean;
  /** Online speech: null when this session listens on this computer only,
   * true while the speech server answers, false while this computer stands in. */
  speechOnline: boolean | null;
}

// Module scoped, because the Perf tab is unmounted whenever another tab is
// showing and a hook-local `useState` would come back null — a blank panel that
// reads as "the sidecar is not reporting". See `lib/liveStore`.
const store = createLiveStore<PipelineHealth | null>(null);
pipeFromSidecar("pipeline_health", store, (m) => {
  return {
    lagSeconds: m.lag_seconds ?? 0,
    droppedChunks: m.dropped_chunks ?? 0,
    healthy: m.healthy ?? true,
    cpuPercent: m.cpu_percent ?? 0,
    rssMb: m.rss_mb ?? 0,
    asrMs: m.asr_ms ?? 0,
    matcherMs: m.matcher_ms ?? 0,
    scorerMs: m.scorer_ms ?? 0,
    scorerSpans: m.scorer_spans ?? 0,
    asrRefused: m.asr_refused ?? false,
    lowPowerMode: m.low_power_mode ?? false,
    batteryPercent: m.battery_percent ?? null,
    onBattery: m.on_battery ?? false,
    speechOnline: m.speech_online ?? null,
  };
});

export function usePipelineHealth(): PipelineHealth | null {
  return useLiveStore(store);
}
