/**
 * The low-latency second listener, for watching.
 *
 * Whisper delivers a word two to three seconds after it is sung, which is why
 * the app catches up instead of anticipating. The fast channel delivers one in
 * about 0.4 s — but on the approved goldens it transcribes sung worship audio
 * far worse (word error 0.85 against 0.22), so it drives nothing. It is shown
 * so the trade can be judged on live input rather than on six recordings.
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

export interface FastWord {
  text: string;
  tStart: number;
  latency: number;
}

export interface FastChannelState {
  words: FastWord[];
  medianLatency: number;
  realtimeFactor: number;
  available: boolean;
  /** Whether its words are currently reaching the matcher. */
  feedsMatcher: boolean;
  /** Why it is not running, when it is not. */
  status: string;
  /** Whether anything has arrived at all this session. */
  seen: boolean;
}

const HISTORY = 60;

const EMPTY: FastChannelState = {
  words: [],
  medianLatency: 0,
  realtimeFactor: 0,
  available: false,
  feedsMatcher: false,
  status: "model_missing",
  seen: false,
};

const store = createLiveStore<FastChannelState>(EMPTY);

pipeFromSidecar("fast_channel", store, (msg, prev) => {
  const incoming = msg.words.map((w) => ({
    text: w.text,
    tStart: w.t_start,
    latency: w.latency,
  }));
  return {
    words: [...incoming, ...prev.words].slice(0, HISTORY),
    medianLatency: msg.median_latency,
    realtimeFactor: msg.realtime_factor,
    available: msg.available,
    feedsMatcher: msg.feeds_matcher,
    status: msg.status ?? "model_missing",
    seen: true,
  };
});

export function useFastChannel(): FastChannelState {
  return useLiveStore(store);
}

export function resetFastChannel(): void {
  store.set(EMPTY);
}

/**
 * Turn the fast channel's words into matcher evidence.
 *
 * Measured on the reference recordings, this trades accuracy for timing: the
 * slide is wrong more often, but the change lands slightly *before* the band
 * rather than half a second after. Which is better depends on the room, so it
 * is a switch and not a default.
 */
export async function setFeedsMatcher(on: boolean): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "set_fast_channel", feed_matcher: on });
  // Optimistic: the sidecar confirms on its next broadcast.
  store.set((prev) => ({ ...prev, feedsMatcher: on }));
}

/**
 * Run (or stop) sherpa-onnx on live frames.
 *
 * Session start only probes whether the model is installed. Inference is
 * the cost — about 2% of a core — so it stays off until this panel is open
 * (or until the operator turns on matcher feed).
 */
export async function setFastListen(on: boolean): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({
    kind: "set_fast_channel",
    feed_matcher: store.get().feedsMatcher,
    listen: on,
  });
}
