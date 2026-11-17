/**
 * Belief state visualisation for the debug panel.
 *
 * Renders the tracker's `belief_state`: top-3 hypotheses with marginals,
 * an ESS sparkline, and the lost indicator.
 */

import { useBeliefState, type PfBelief, type PfHypothesis } from "@/lib/belief";
import { setBlankWhenIdle, useSingingState } from "@/lib/singingState";
import { cn } from "@/lib/utils";
import { useToggleRequest } from "@/components/debug/useToggleRequest";

/**
 * Whether the words being heard are the words on screen, and the switch that
 * acts on it. The same question as the belief below, asked one level up: not
 * "which slide" but "any slide at all".
 */
function SingingRow() {
  const state = useSingingState();
  const onToggle = useToggleRequest(
    "blanking when nobody is singing",
    setBlankWhenIdle,
  );
  // Hooks must run before any early return.
  if (!state.seen) return null;
  const label =
    state.reason === "silence"
      ? "Nobody is singing"
      : state.reason === "not_these_words"
        ? "Hearing something else"
        : "Following the singing";
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-3 py-2 text-xs">
      <span
        className={cn(
          "rounded px-2 py-0.5",
          state.singing
            ? "bg-emerald-500/15 text-emerald-300"
            : "bg-amber-500/15 text-amber-300",
        )}
      >
        {label}
      </span>
      {state.blanking && (
        <span className="rounded bg-white/10 px-2 py-0.5 text-zinc-300">
          screen blanked
        </span>
      )}
      {/* How close to the line it is running. Blanking starts at 0.80 and
          stops at 0.40; anything under 0.10 is comfortably sung. */}
      <span className="tabular-nums text-zinc-500">
        talking {state.score.toFixed(2)}
      </span>
      <label className="ml-auto flex items-center gap-1.5 text-zinc-400">
        <input
          type="checkbox"
          checked={state.enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        Blank the screen when nobody is singing
      </label>
    </div>
  );
}

export function BeliefStateTab() {
  const pf = useBeliefState();

  if (pf.latest === null) {
    return (
      <div className="flex h-full items-center justify-center px-6 py-8 text-center text-xs text-zinc-500">
        Waiting for the matcher to start. Begin a service with the AI
        AI on (the AI button in the service) to see live belief.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <SingingRow />
      <PfView belief={pf.latest} essHistory={pf.essHistory} />
    </div>
  );
}


function PfView({
  belief,
  essHistory,
}: {
  belief: PfBelief;
  essHistory: number[];
}) {
  return (
    <div className="flex h-full flex-col gap-3 px-3 py-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-zinc-400">
          Tracker ·{" "}
          <span className="text-zinc-100">ESS {belief.ess.toFixed(1)}</span>
        </span>
        {belief.isLost && (
          <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-200">
            Lost
          </span>
        )}
      </div>
      <ol className="space-y-1">
        {belief.top3.map((h, i) => (
          <HypothesisRow key={`${h.itemIdx}-${h.slideIdx}-${i}`} hypothesis={h} rank={i + 1} />
        ))}
      </ol>
      <div>
        <div className="mb-1 flex items-center justify-between text-zinc-500">
          <span>ESS over time</span>
          <span>last {essHistory.length} updates</span>
        </div>
        <EssSparkline values={essHistory} />
      </div>
    </div>
  );
}


function HypothesisRow({
  hypothesis,
  rank,
}: {
  hypothesis: PfHypothesis;
  rank: number;
}) {
  const pct = Math.max(0, Math.min(100, hypothesis.marginal * 100));
  const isTop = rank === 1;
  return (
    <li className="space-y-0.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className={cn(isTop && "font-semibold text-emerald-300")}>
          #{rank} · item {hypothesis.itemIdx} slide {hypothesis.slideIdx}
        </span>
        <span className={cn("tabular-nums text-zinc-500", isTop && "text-emerald-200")}>
          {(hypothesis.marginal * 100).toFixed(1)}%
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-900">
        <div
          className={cn(
            "h-full transition-[width] duration-100",
            isTop ? "bg-emerald-400" : "bg-sky-500/80",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </li>
  );
}


function EssSparkline({ values }: { values: number[] }) {
  // The series is most-recent-first; flip so time flows left → right.
  const series = [...values].reverse();
  if (series.length === 0) {
    return <div className="h-8 rounded bg-zinc-900" />;
  }
  const max = Math.max(...series, 1);
  return (
    <svg
      className="h-8 w-full"
      viewBox={`0 0 ${Math.max(1, series.length - 1)} 100`}
      preserveAspectRatio="none"
      role="img"
      aria-label="ESS history"
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        className="text-sky-400"
        points={series
          .map((v, i) => `${i},${100 - (v / max) * 100}`)
          .join(" ")}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
