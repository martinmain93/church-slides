/**
 * Performance tab — what the sidecar is costing, right now.
 *
 * Cost has to be observable while a service runs. A UI render loop once
 * starved this pipeline for an entire session and the only visible symptom
 * was "the AI isn't working"; the diagnosis took hours of guessing because
 * nothing reported CPU, memory, or where the time was going.
 *
 * The headline number is the **ASR budget**: one pass has to finish inside the
 * step interval that feeds it, or the pipeline falls behind and the capture
 * queue starts discarding audio. Everything else is context for that.
 *
 * The Engine section below reports the other direction — not what the sidecar
 * costs, but what it is about to *do*. Cost and intent belong on one screen
 * because they interact: a pipeline near its budget delivers words late, and
 * late words are why a slide change lands after the band has moved on.
 */

import { useEffect, useRef, useState } from "react";

import { type EngineTelemetry, useEngineTelemetry } from "@/lib/engineTelemetry";
import { usePipelineHealth } from "@/lib/pipelineHealth";
import { cn } from "@/lib/utils";

//: The wrapper asks for a pass every second of audio; a pass must fit inside
//: that or the deficit compounds.
const ASR_BUDGET_MS = 1000;

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <div className="min-w-0">
        <div className="text-xs text-zinc-300">{label}</div>
        {hint && <div className="text-[10px] text-zinc-500">{hint}</div>}
      </div>
      <div className={cn("font-mono text-sm tabular-nums", tone ?? "text-zinc-100")}>
        {value}
      </div>
    </div>
  );
}

function Bar({ fraction, tone }: { fraction: number; tone: string }) {
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded bg-white/10">
      <div
        className={cn("h-full", tone)}
        style={{ width: `${Math.max(0, Math.min(1, fraction)) * 100}%` }}
      />
    </div>
  );
}

/**
 * A countdown that keeps ticking between sidecar updates.
 *
 * Belief updates land about once a second, so a raw readout would freeze and
 * jump — unreadable for the thing this is for, which is watching whether the
 * countdown reaches zero before the band does. Extrapolating is sound here
 * because the quantity really is seconds of audio elapsing in real time; the
 * value resets to ground truth on every update, so drift cannot accumulate.
 */
function useTickingCountdown(engine: EngineTelemetry | null): number | null {
  // Anchored on the *message*, not on its value: two consecutive updates can
  // legitimately carry the same `secondsRemaining`, and keying on the number
  // would leave the older anchor in place and keep draining the countdown
  // past what the engine actually believes.
  const anchor = useRef<{ from: EngineTelemetry; value: number; at: number } | null>(
    null,
  );
  const [, force] = useState(0);
  const seconds = engine?.secondsRemaining ?? null;

  if (engine === null || seconds === null) {
    anchor.current = null;
  } else if (anchor.current?.from !== engine) {
    anchor.current = { from: engine, value: seconds, at: Date.now() };
  }

  useEffect(() => {
    if (seconds === null) return;
    const id = setInterval(() => force((n) => n + 1), 100);
    return () => clearInterval(id);
  }, [seconds]);

  if (anchor.current === null) return null;
  return Math.max(0, anchor.current.value - (Date.now() - anchor.current.at) / 1000);
}

function EngineSection() {
  const engine = useEngineTelemetry();
  const countdown = useTickingCountdown(engine);

  if (!engine) {
    return (
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          Engine
        </div>
        <div className="text-xs text-zinc-400">
          Nothing yet — reported on each belief update, once a matcher is
          running.
        </div>
      </div>
    );
  }

  const lead = engine.leadTimeMs / 1000;

  // Whether the *engine* has seen the countdown elapse — not whether the
  // local extrapolation has. These differ constantly: the ticker runs at
  // 10 Hz while belief updates arrive only as words do, so between updates
  // the displayed countdown routinely dips under the lead time while the
  // engine's last word on it predates the crossing. Reading `advancing` as
  // "blocked" in that gap accuses `order_is_predictive` of something it
  // never said.
  const engineSawItElapse =
    engine.secondsRemaining !== null && engine.secondsRemaining < lead;
  const blocked = engineSawItElapse && !engine.advancing;
  // Ticked past the threshold locally, but the engine has not re-evaluated
  // since — it cannot, until the next words arrive.
  const awaitingWords =
    !engineSawItElapse && countdown !== null && countdown < lead;

  const countdownTone =
    countdown === null
      ? "text-zinc-500"
      : engine.advancing
        ? "text-emerald-400"
        : blocked
          ? "text-amber-400"
          : "text-zinc-100";

  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
        Engine
      </div>
      <Metric
        label="Next slide in"
        hint={
          blocked
            ? `past the ${engine.leadTimeMs.toFixed(0)} ms lead time, but order is not predictive here`
            : awaitingWords
              ? "elapsed — waiting on the next words before it can act"
              : `commits at ${engine.leadTimeMs.toFixed(0)} ms`
        }
        value={countdown === null ? "—" : `${countdown.toFixed(1)} s`}
        tone={countdownTone}
      />
      {countdown !== null && (
        <Bar
          fraction={1 - Math.min(1, countdown / Math.max(lead, 0.001) / 4)}
          tone={engine.advancing ? "bg-emerald-400" : "bg-zinc-500"}
        />
      )}
      {engine.endingProbability != null && (
        <Metric
          label="Belief says ending"
          hint={
            engine.statesOnSlide <= 1
              ? "only one state on this slide — no distribution to weigh"
              : `share of ${engine.statesOnSlide} states past the lead time; ` +
                `commits at ${((engine.endingProbabilityBar ?? 0) * 100).toFixed(0)}%`
          }
          value={`${(engine.endingProbability * 100).toFixed(0)} %`}
          tone={
            engine.endingProbability >= (engine.endingProbabilityBar ?? 1)
              ? "text-emerald-400"
              : undefined
          }
        />
      )}
      {engine.stalledSeconds != null && engine.stallAfterSeconds != null && (
        <Metric
          label="Screen ignoring the belief"
          hint={`overridden at ${engine.stallAfterSeconds.toFixed(0)} s — the freeze watchdog`}
          value={`${engine.stalledSeconds.toFixed(1)} s`}
          tone={
            engine.stalledSeconds > engine.stallAfterSeconds * 0.6
              ? "text-amber-400"
              : undefined
          }
        />
      )}
      {engine.pendingItemIdx != null && (
        <Metric
          label="Another song proposed"
          hint="held until it clears the higher cross-song bar"
          value={`item ${engine.pendingItemIdx} (${engine.pendingItemObservations})`}
          tone="text-amber-400"
        />
      )}
      <Metric
        label="Sung tempo"
        hint={
          engine.wordsOnSlide !== null && engine.position !== null
            ? `${(engine.position * 100).toFixed(0)}% through ${engine.wordsOnSlide} words`
            : "words per second, counted off the transcript"
        }
        value={engine.rateWps === null ? "—" : `${engine.rateWps.toFixed(2)} w/s`}
      />
      <Metric
        label="Lead in use"
        hint={
          `lag ${(engine.leadLagMs / 1000).toFixed(1)}s + gap ` +
          `${(engine.leadGapMs / 1000).toFixed(1)}s + margin ` +
          `${(engine.targetMarginMs / 1000).toFixed(1)}s — derived, not set`
        }
        value={`${(engine.leadTimeMs / 1000).toFixed(1)} s`}
      />
      {engine.slidesAhead > 1 && (
        <div className="mt-1 text-[10px] text-zinc-400">
          Projecting {engine.slidesAhead} slides ahead — the lead outlasts the
          next slide, so a one-step rule could never reach it in time.
        </div>
      )}
      <Metric
        label="Transcript lag"
        hint={
          engine.latencySamples > 0
            ? `sung to actionable; p90 ${(engine.latencyP90Ms ?? 0).toFixed(0)} ms over ${engine.latencySamples} words`
            : "sung to actionable — waiting for the first words"
        }
        value={
          engine.latencyMedianMs === null
            ? "—"
            : `${(engine.latencyMedianMs / 1000).toFixed(1)} s`
        }
        tone={
          engine.latencyMedianMs !== null && engine.latencyMedianMs > 4000
            ? "text-red-400"
            : engine.latencyMedianMs !== null && engine.latencyMedianMs > 2500
              ? "text-amber-400"
              : undefined
        }
      />
    </div>
  );
}

export function PerfTab() {
  const health = usePipelineHealth();

  if (!health) {
    return (
      <div className="space-y-3 p-3">
        <div className="text-xs text-zinc-400">
          Waiting for the sidecar. Performance is reported every couple of
          seconds while an audio session is running.
        </div>
        <EngineSection />
      </div>
    );
  }

  const asrFraction = health.asrMs / ASR_BUDGET_MS;
  const asrTone =
    asrFraction > 1 ? "text-red-400" : asrFraction > 0.7 ? "text-amber-400" : "text-emerald-400";

  return (
    <div className="space-y-3 p-3">
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          ASR budget
        </div>
        <Metric
          label="Time per pass"
          hint={`must stay under ${ASR_BUDGET_MS} ms or audio is dropped`}
          value={`${health.asrMs.toFixed(0)} ms`}
          tone={asrTone}
        />
        <Bar
          fraction={asrFraction}
          tone={
            asrFraction > 1
              ? "bg-red-400"
              : asrFraction > 0.7
                ? "bg-amber-400"
                : "bg-emerald-400"
          }
        />
        {asrFraction > 0.7 && (
          <div className="mt-1 text-[10px] text-amber-400">
            Close to the budget — a smaller model (Transcript tab) buys
            headroom.
          </div>
        )}
      </div>

      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          Pipeline
        </div>
        <Metric
          label="Behind the room"
          value={`${health.lagSeconds.toFixed(1)} s`}
          tone={health.lagSeconds > 2 ? "text-amber-400" : undefined}
        />
        <Metric
          label="Audio discarded"
          hint="the capture queue holds 5 s before it drops"
          value={String(health.droppedChunks)}
          tone={health.droppedChunks > 0 ? "text-red-400" : undefined}
        />
        <Metric
          label="Matcher per update"
          value={`${health.matcherMs.toFixed(1)} ms`}
        />
        <Metric
          label="Score follower per pass"
          hint={
            health.scorerSpans > 0
              ? `${health.scorerSpans} spans · shares the GPU queue with ASR`
              : "not running (no lead sheets or learned songs this session)"
          }
          value={health.scorerSpans > 0 ? `${health.scorerMs.toFixed(0)} ms` : "off"}
        />
      </div>

      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
          Sidecar process
        </div>
        <Metric
          label="CPU"
          hint="share of one core"
          value={`${health.cpuPercent.toFixed(0)} %`}
          tone={health.cpuPercent > 90 ? "text-amber-400" : undefined}
        />
        <Metric label="Memory (peak RSS)" value={`${health.rssMb.toFixed(0)} MB`} />
      </div>

      <EngineSection />
    </div>
  );
}
