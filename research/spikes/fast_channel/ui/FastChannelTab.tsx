/**
 * The low-latency listener, side by side with what it costs and what it gets
 * wrong. It drives nothing — see `lib/fastChannel` — so this panel exists to
 * answer one question on live input: is a word arriving fast enough, and
 * accurately enough, to be worth acting on?
 */

import { useEffect } from "react";
import { setFeedsMatcher, setFastListen, useFastChannel } from "@/lib/fastChannel";
import { useTranscriptStream } from "@/lib/transcriptStream";
import { VISIBLE_WORDS } from "@/components/debug/TranscriptTab";
import type { Word } from "@/types/generated";
import { useToggleRequest } from "@/components/debug/useToggleRequest";

export function FastChannelTab() {
  const fast = useFastChannel();
  const stream = useTranscriptStream();
  const onToggle = useToggleRequest(
    "the fast channel driving slides",
    setFeedsMatcher,
  );

  useEffect(() => {
    void setFastListen(true).catch(console.error);
    return () => {
      void setFastListen(false).catch(() => undefined);
    };
  }, []);

  if (!fast.available) {
    // Say which of the three things is missing, and what to do about it —
    // "not configured" sends people hunting through settings that don't exist.
    const reason =
      fast.status === "runtime_missing"
        ? "The speech runtime isn't installed yet."
        : fast.status === "load_failed"
          ? "The model is there but failed to load — check the sidecar log."
          : fast.status === "idle"
            ? "The model is installed. Starting it for this panel…"
            : !fast.seen
              ? "Waiting for a session to start."
              : "The model hasn't been downloaded yet.";
    const fixable =
      fast.status === "runtime_missing" || fast.status === "model_missing";
    return (
      <div className="flex-1 overflow-y-auto px-4 py-3 text-xs text-zinc-400">
        <p className="mb-2 text-zinc-300">Fast channel is off. {reason}</p>
        {fixable && (
          <>
            <p className="mb-1">One command sets it up:</p>
            <pre className="mb-3 overflow-x-auto rounded bg-black/40 px-2 py-1.5 text-[11px] text-zinc-200">
              uv run --project sidecar python research/spikes/fast_channel/setup_fast_channel.py
            </pre>
            <p className="mb-2">Then start a session.</p>
          </>
        )}
        <p>
          It is a second, faster listener — it hears a word about half a second
          after you sing it, against two to three for the main one. It hears
          words far less accurately, so it is off unless you switch it on.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 text-xs">
      <label className="mb-3 flex items-start gap-2 rounded border border-white/10 bg-white/5 px-2 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={fast.feedsMatcher}
          disabled={!fast.available}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <span className="font-sans leading-tight">
          <span className="text-zinc-200">Let it drive slide changes</span>
          <span className="block text-[10px] text-zinc-400">
            Slides land slightly before the band instead of half a second
            after, but land on the wrong slide more often. Off by default.
          </span>
        </span>
      </label>
      <div className="mb-3 grid grid-cols-3 gap-2">
        <Stat
          label="Delay before we hear a word"
          value={`${fast.medianLatency.toFixed(2)}s`}
          hint="Whisper: 2-3s"
        />
        <Stat
          label="CPU used"
          value={`${(fast.realtimeFactor * 100).toFixed(1)}%`}
          hint="Whisper: ~31%"
        />
        <Stat
          label="Words heard"
          value={`${fast.words.length}`}
          hint={`Whisper: ${stream.finalWords.length}`}
        />
      </div>
      <div className="mb-1 font-sans text-[11px] uppercase tracking-wide text-zinc-500">
        Fast channel — first, but rough
      </div>
      <div className="mb-3 flex flex-wrap gap-x-1 font-mono text-[12px] text-emerald-200">
        {fast.words.length === 0 ? (
          <span className="text-zinc-500">nothing yet</span>
        ) : (
          fast.words
            .slice(0, VISIBLE_WORDS)
            .map((w, i) => (
              <span key={`${i}-${w.tStart}`} title={`${w.latency.toFixed(2)}s late`}>
                {w.text}{" "}
              </span>
            ))
        )}
      </div>
      <div className="mb-1 font-sans text-[11px] uppercase tracking-wide text-zinc-500">
        Whisper — slower, and what actually drives the slides
      </div>
      <div className="flex flex-wrap gap-x-1 font-mono text-[12px] text-zinc-100">
        {stream.finalWords.length === 0 ? (
          <span className="text-zinc-500">nothing yet</span>
        ) : (
          // Chronological, left-to-right — same as `FinalWordsList` in the
          // Transcript tab. This used to `.reverse()` the slice, which
          // reverses word *order* too, not just which end is visible —
          // "go to the chorus" rendered as "chorus the to go".
          stream.finalWords
            .slice(-VISIBLE_WORDS)
            .map((w: Word, i: number) => (
              <span key={`${i}-${w.t_start}`}>{w.text} </span>
            ))
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded border border-white/10 bg-white/5 px-2 py-1.5">
      <div className="font-sans text-[10px] leading-tight text-zinc-400">
        {label}
      </div>
      <div className="font-mono text-[15px] text-zinc-100">{value}</div>
      <div className="font-sans text-[10px] text-zinc-500">{hint}</div>
    </div>
  );
}
