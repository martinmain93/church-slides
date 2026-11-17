/**
 * Live engine-telemetry subscription — what the matcher is about to do.
 *
 * Every other debug surface reports the past: the belief, the decisions that
 * fired, the transcript that arrived. This reports the three quantities that
 * decide the *next* slide change, so a run that feels wrong can be diagnosed
 * while it is still going.
 *
 * Each of the three has been silently broken at some point, with no symptom
 * anywhere in the app beyond "it isn't switching when it should": the word
 * rate pinned at its floor, the countdown that divides by it inflated
 * sixfold, and a delivery lag nobody had ever measured.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";

export interface EngineTelemetry {
  /**
   * Seconds until the current slide is projected to end, or null where there
   * is nothing to project to — the blank lead-in, or the last slide of a song.
   */
  secondsRemaining: number | null;
  /**
   * The lead the commit rule is using right now, and the two measurements it
   * is derived from. Reporting only the total would hide which term moved
   * when the timing goes wrong — and the first two move continuously.
   */
  leadTimeMs: number;
  leadLagMs: number;
  leadGapMs: number;
  targetMarginMs: number;
  /** Slides between the belief's slide and the target. >1 means the lead
   * outlasted a short slide entirely. */
  slidesAhead: number;
  /**
   * Countdown elapsed *and* song order still predictive. A near-zero
   * countdown with this false means the projection was blocked, which from
   * outside looks identical to it simply not having elapsed.
   */
  advancing: boolean;
  targetSlideIdx: number | null;
  position: number | null;
  wordsOnSlide: number | null;
  /** Tempo the countdown divides by, words per second. */
  rateWps: number | null;
  /**
   * Fraction of the belief saying the current slide ends within the lead
   * time, and the bar it must clear. Null means the engine fell back to the
   * point estimate. A countdown cannot show the difference between "nearly
   * ready" and "split down the middle"; this can.
   */
  endingProbability: number | null;
  endingProbabilityBar: number | null;
  /** Tracker states the belief covers for this slide. 1 = no distribution. */
  statesOnSlide: number;
  /** Seconds the belief has pointed at a slide the screen is ignoring. */
  stalledSeconds: number | null;
  stallAfterSeconds: number | null;
  /** Another song under consideration, not yet past the cross-song bar. */
  pendingItemIdx: number | null;
  pendingItemObservations: number;
  /** Sung-time to actionable-time, over a rolling window of recent words. */
  latencyMedianMs: number | null;
  latencyP90Ms: number | null;
  latencySamples: number;
}

// Module scoped: the Perf tab is unmounted while another tab shows, and a
// hook-local `useState` would come back null — a blank panel reading as "the
// sidecar is not reporting". See `lib/liveStore`.
const store = createLiveStore<EngineTelemetry | null>(null);
pipeFromSidecar("engine_telemetry", store, (m) => {
  return {
    secondsRemaining: m.seconds_remaining ?? null,
    leadTimeMs: m.lead_time_ms ?? 0,
    leadLagMs: m.lead_lag_ms ?? 0,
    leadGapMs: m.lead_gap_ms ?? 0,
    targetMarginMs: m.target_margin_ms ?? 0,
    slidesAhead: m.slides_ahead ?? 0,
    advancing: m.advancing ?? false,
    targetSlideIdx: m.target_slide_idx ?? null,
    position: m.position ?? null,
    wordsOnSlide: m.words_on_slide ?? null,
    rateWps: m.rate_wps ?? null,
    endingProbability: m.ending_probability ?? null,
    endingProbabilityBar: m.ending_probability_bar ?? null,
    statesOnSlide: m.states_on_slide ?? 0,
    stalledSeconds: m.stalled_seconds ?? null,
    stallAfterSeconds: m.stall_after_seconds ?? null,
    pendingItemIdx: m.pending_item_idx ?? null,
    pendingItemObservations: m.pending_item_observations ?? 0,
    latencyMedianMs: m.latency_median_ms ?? null,
    latencyP90Ms: m.latency_p90_ms ?? null,
    latencySamples: m.latency_samples ?? 0,
  };
});

export function useEngineTelemetry(): EngineTelemetry | null {
  return useLiveStore(store);
}
