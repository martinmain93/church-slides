/**
 * Band-pass the inspector's playback so one onset band can be heard alone.
 *
 * The analysis bands are rectangular STFT ranges. Three high-passes and three
 * low-passes at those same edges are steep enough to leave that range in
 * front, and they follow the playhead with no extra decode.
 */

export type BandSolo = {
  setBand: (hzLo: number, hzHi: number) => void;
  clear: () => void;
  resume: () => void;
  close: () => void;
  /** Audio-thread clock, in seconds. Keeps advancing while the main thread is busy. */
  outputTime: () => number;
};

export type PlayAnchor = { media: number; output: number };

/**
 * How far the element clock may lag the output clock before we stop trusting it.
 * A main-thread hitch freezes `audio.currentTime` while the speakers keep going,
 * and WebKit does not catch that clock up until pause. A quarter-second is more
 * than a frame of jitter and less than a beat.
 */
const CLOCK_STALL_SEC = 0.25;

/**
 * Playhead position that stays with the speakers.
 *
 * While the two clocks agree, this returns the element time and re-anchors, so
 * healthy playback is unchanged. Once the output clock pulls ahead, it keeps
 * the anchor and rides the output clock until the element clock catches up.
 */
export function followOutputClock(
  mediaTime: number,
  outputTime: number,
  anchor: PlayAnchor | null,
): { time: number; anchor: PlayAnchor } {
  if (!anchor) return { time: mediaTime, anchor: { media: mediaTime, output: outputTime } };
  const predicted = anchor.media + (outputTime - anchor.output);
  if (predicted > mediaTime + CLOCK_STALL_SEC) return { time: predicted, anchor };
  return { time: mediaTime, anchor: { media: mediaTime, output: outputTime } };
}

const STAGES = 3;

export function attachBandSolo(el: HTMLAudioElement): BandSolo {
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AC();
  const source = ctx.createMediaElementSource(el);
  const dry = ctx.createGain();
  const wet = ctx.createGain();
  wet.gain.value = 0;
  const filters: BiquadFilterNode[] = [];
  let node: AudioNode = source;
  for (let i = 0; i < STAGES; i++) {
    const hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.Q.value = 0.707;
    node.connect(hp);
    node = hp;
    filters.push(hp);
  }
  for (let i = 0; i < STAGES; i++) {
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.Q.value = 0.707;
    node.connect(lp);
    node = lp;
    filters.push(lp);
  }
  source.connect(dry);
  dry.connect(ctx.destination);
  node.connect(wet);
  wet.connect(ctx.destination);

  const highpasses = filters.filter((f) => f.type === "highpass");
  const lowpasses = filters.filter((f) => f.type === "lowpass");

  return {
    setBand(hzLo: number, hzHi: number) {
      const now = ctx.currentTime;
      for (const f of highpasses) f.frequency.setTargetAtTime(Math.max(10, hzLo), now, 0.01);
      for (const f of lowpasses) f.frequency.setTargetAtTime(hzHi, now, 0.01);
      dry.gain.setTargetAtTime(0, now, 0.02);
      wet.gain.setTargetAtTime(1, now, 0.02);
      void ctx.resume();
    },
    clear() {
      const now = ctx.currentTime;
      dry.gain.setTargetAtTime(1, now, 0.02);
      wet.gain.setTargetAtTime(0, now, 0.02);
    },
    resume() {
      void ctx.resume();
    },
    close() {
      void ctx.close();
    },
    outputTime() {
      return ctx.currentTime;
    },
  };
}
