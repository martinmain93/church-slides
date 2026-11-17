/**
 * Whether anyone is singing the words on the screen.
 *
 * Two ways the answer is no, and they are worth telling apart in the UI:
 * silence (nothing is arriving at all) and *not these words* (someone is
 * talking, or singing something else). The first is noticed by the clock, the
 * second by how badly what was heard matches what is displayed.
 *
 * Acting on it — blanking the display — is on by default (the engine's
 * `blank_when_idle`; `useAutoBlank` is what blanks the screen). Measured with
 * each recording held out of the detector's own fit, it is wrong on 1.3% of
 * events during real worship.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";

export interface SingingState {
  singing: boolean;
  /** "singing", "silence" or "not_these_words". */
  reason: string;
  /** Whether the display is actually being blanked *right now* as a
   * result. Not the same as whether the feature is turned on — see
   * `enabled`. */
  blanking: boolean;
  /** Optimistic — set locally on toggle, not mirrored back by the sidecar.
   * The checkbox must bind to this, not to `blanking`: while the feature
   * is on but the tracker is currently following the singing correctly,
   * `blanking` is false, and a checkbox bound to it would look like it
   * "won't check" — it unchecks itself the instant singing resumes. */
  enabled: boolean;
  /** The smoothed talking score behind the call, 0..1. */
  score: number;
  /** Whether anything has been reported yet this session. */
  seen: boolean;
}

const EMPTY: SingingState = {
  singing: true,
  reason: "singing",
  blanking: false,
  enabled: true,
  score: 0,
  seen: false,
};

const store = createLiveStore<SingingState>(EMPTY);

pipeFromSidecar("singing_state", store, (msg, prev) => {
  return {
    singing: msg.singing,
    reason: msg.reason,
    blanking: msg.blanking,
    enabled: prev.enabled,
    score: msg.score ?? 0,
    seen: true,
  };
});

export function useSingingState(): SingingState {
  return useLiveStore(store);
}

export function resetSingingState(): void {
  store.set(EMPTY);
}

/** Blank the display while nobody is singing the words on it. */
export async function setBlankWhenIdle(enabled: boolean): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "set_blank_when_idle", enabled });
  store.set((prev) => ({ ...prev, enabled }));
}
