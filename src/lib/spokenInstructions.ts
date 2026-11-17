/**
 * Spoken instructions — "let's go back to verse one" as a slide change,
 * instead of the tracker trying (and failing) to match spoken words against
 * sung lyrics.
 *
 * Off by default and unmeasured on real utterances — the roadmap's own gate
 * on this feature ("collect real utterances first") hasn't been met. What
 * *is* measured, on synthetic typed utterances against the shipped model
 * (llama3.2:3b via a local ollama): 10/10 correctly silent on ordinary
 * between-songs talk, 7/10 correct on genuine commands. See
 * `docs/done/SPOKEN_INSTRUCTIONS.md` and `decision.instructions`'s module
 * docstring for the full write-up, including the finding that an explicit
 * "do nothing" tool is not optional — without one, the same model called a
 * tool on 10 of 10 sentences that weren't requests at all.
 *
 * Module-scoped like the other live stores, so switching debug tabs does not
 * throw the history away. See `lib/liveStore`.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";

export interface SpokenInstructionEvent {
  tSeconds: number;
  utterance: string;
  itemIdx: number | null;
  slideIdx: number | null;
  applied: boolean;
  songId: string | null;
  title: string | null;
  score: number | null;
  /** A passage asked for, and how ("off" = taken down). */
  scriptureReference: string | null;
  scriptureDisplay: string | null;
}

export interface SpokenInstructionsState {
  /** Optimistic — set locally on toggle, not mirrored back by the sidecar.
   * Whether an utterance actually gets heard depends on someone talking. */
  enabled: boolean;
  recent: SpokenInstructionEvent[];
  /** Whether anything has arrived at all this session. */
  seen: boolean;
}

const HISTORY = 15;

const EMPTY: SpokenInstructionsState = {
  enabled: false,
  recent: [],
  seen: false,
};

const store = createLiveStore<SpokenInstructionsState>(EMPTY);

pipeFromSidecar("spoken_instruction", store, (msg, prev) => {
  const event: SpokenInstructionEvent = {
    tSeconds: msg.t_seconds,
    utterance: msg.utterance,
    itemIdx: msg.item_idx ?? null,
    slideIdx: msg.slide_idx ?? null,
    applied: msg.applied ?? false,
    songId: msg.song_id ?? null,
    title: msg.title ?? null,
    score: msg.score ?? null,
    scriptureReference: msg.scripture_reference ?? null,
    scriptureDisplay: msg.scripture_display ?? null,
  };
  return {
    enabled: prev.enabled,
    recent: [event, ...prev.recent].slice(0, HISTORY),
    seen: true,
  };
});

// A service decides at start (the saved setting, or on when a hosted key is on
// file) and says so with "running"; follow it, so the switch shows the truth.
pipeFromSidecar("audio_session_state", store, (msg, prev) =>
  msg.state === "running" && msg.spoken_instructions != null
    ? { ...prev, enabled: msg.spoken_instructions }
    : prev,
);

export function useSpokenInstructions(): SpokenInstructionsState {
  return useLiveStore(store);
}

export function resetSpokenInstructions(): void {
  store.set(EMPTY);
}

/**
 * Let a local model act on things said between songs. Off by default: it
 * needs an ollama server running locally with a model already pulled (see
 * `docs/done/SPOKEN_INSTRUCTIONS.md`), and it moves the slide on its own
 * judgement — the same authority as an operator override.
 */
export async function setSpokenInstructions(enabled: boolean): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "set_spoken_instructions", enabled });
  store.set((prev) => ({ ...prev, enabled }));
}
