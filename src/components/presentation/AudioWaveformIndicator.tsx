/**
 * Live audio-input indicator for the presentation view.
 *
 * Renders a small animated bar waveform whose heights track the most recent
 * input levels (RMS dBFS) broadcast by the sidecar's `audio_level` stream, so
 * the operator can see at a glance that the mic / board feed is actually being
 * picked up during a service. Goes dim with a muted-mic icon when the signal
 * is silent or no session is feeding levels.
 */

import { useMemo } from "react";
import { AlertTriangle, Mic, MicOff } from "lucide-react";

import { useAudioLevels } from "@/lib/audioLevels";
import { usePipelineHealth } from "@/lib/pipelineHealth";
import { cn } from "@/lib/utils";

const BARS = 9;
//: Below this RMS we treat the input as "no speech detected" (room noise /
//: silence). A clean vocal sitting around -30..-12 dBFS reads as active.
const ACTIVE_THRESHOLD_DBFS = -50;

/** Map an RMS dBFS reading to a 0..1 bar fraction over a ~-60..0 dB range. */
function dbfsToFraction(dbfs: number): number {
  if (!Number.isFinite(dbfs)) return 0;
  return Math.max(0, Math.min(1, (dbfs + 60) / 60));
}

export function AudioWaveformIndicator() {
  const { history, latest, connected } = useAudioLevels(BARS * 3);
  const health = usePipelineHealth();

  // The mic is "live" whenever the capture session is feeding us level
  // samples — i.e. the device is open and hooked up. We only cross it out
  // when nothing is being captured (no session / engine offline), NOT when
  // it's simply quiet.
  const micLive = connected && latest != null;
  // Sound actually present right now (drives the bar animation + color).
  const sound =
    micLive &&
    Number.isFinite(latest!.rms_dbfs) &&
    latest!.rms_dbfs > ACTIVE_THRESHOLD_DBFS;

  // One bar per recent sample (scrolls right-to-left as speech moves). Pad the
  // left with silence when we don't yet have BARS samples.
  const fractions = useMemo(() => {
    const recent = history.slice(-BARS).map((s) => dbfsToFraction(s.rms_dbfs));
    while (recent.length < BARS) recent.unshift(0);
    return recent;
  }, [history]);

  // Overload deserves its own signal rather than being folded into the level
  // meter: a starved pipeline can still show healthy input levels while the
  // matcher receives a transcript full of holes.
  const degraded = health != null && !health.healthy;

  return (
    <div className="flex items-center gap-2">
    <div
      data-testid="audio-waveform"
      title={
        !micLive
          ? "No audio input — device not connected or session not started"
          : sound
          ? "Audio detected"
          : "Mic live — waiting for sound"
      }
      aria-label={
        !micLive ? "No audio input" : sound ? "Audio detected" : "Mic live, no sound"
      }
      className={cn(
        "flex items-center gap-2 rounded-md bg-black/40 px-3 py-1.5 backdrop-blur transition-colors",
        micLive ? "text-emerald-300" : "text-white/40",
      )}
    >
      {/* Green mic whenever the input is live; crossed-out only when there's
          genuinely no capture happening. */}
      {micLive ? <Mic className="size-3.5" /> : <MicOff className="size-3.5" />}
      <div className="flex h-4 items-center gap-[2px]" aria-hidden>
        {fractions.map((f, i) => (
          <span
            key={i}
            className={cn(
              "w-[3px] rounded-full transition-[height,background-color] duration-100",
              // Bars light up green with sound; stay a dim baseline when the
              // mic is live but quiet, or when there's no input at all.
              sound ? "bg-emerald-400" : micLive ? "bg-emerald-400/30" : "bg-white/25",
            )}
            // Floor so silent bars read as a thin baseline, not nothing.
            style={{ height: `${Math.max(12, f * 100)}%` }}
          />
        ))}
      </div>
    </div>
    {degraded && (
      <div
        data-testid="pipeline-degraded"
        title={
          `The AI is ${health!.lagSeconds.toFixed(1)}s behind the room` +
          (health!.droppedChunks > 0
            ? ` and has lost ${health!.droppedChunks} chunk(s) of audio.`
            : ".") +
          " Speech recognition can't keep up on this computer: close other busy apps."
        }
        className="flex items-center gap-1.5 rounded-md bg-amber-500/20 px-2 py-1.5 text-amber-300 backdrop-blur"
      >
        <AlertTriangle className="size-3.5" />
        <span className="text-xs font-medium tabular-nums">
          {health!.lagSeconds.toFixed(1)}s behind
          {/* Dropped chunks are the proof that lag is real: the capture queue
              holds 5s, so sustained lag MUST discard audio. Lag with no drops
              means the pipeline is coping and the number is misleading. */}
          {health!.droppedChunks > 0 && ` · ${health!.droppedChunks} lost`}
        </span>
      </div>
    )}
    </div>
  );
}
