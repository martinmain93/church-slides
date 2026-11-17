/**
 * The learning look-back in the Model inspector (docs/done/LEARNED_REFERENCES.md).
 *
 * The look-back is what the app learns from: after the fact, with the words
 * that came later as well as before, it works out which line the band was on
 * for every word heard. These views lay that against the golden so a human can
 * see where it is right, where it is unsure, and where it is wrong:
 *
 *   - `LookbackPassLane`, above the waveform: one span per pass through a line,
 *     shaded by confidence, outlined red where the golden disagrees.
 *   - `LookbackStatePanel`, beside it: at the playhead, the word heard and the
 *     word it was placed on, the odds behind that, the transcript around it,
 *     and the song's scorecard against the golden.
 *
 * Slides are numbered from 1 here, as everywhere else in the inspector.
 */

import { useMemo, type RefObject } from "react";

import { useFollowedTime } from "@/components/recordings/MusicLayers";
import { formatTime, timeToViewFraction, type ViewWindow } from "@/lib/recordings/timeline";
import { cn } from "@/lib/utils";
import type {
  GroundTruthEvent,
  LookbackItemView,
  LookbackWordView,
  SetlistItemSnapshot,
} from "@/types/generated";

/** A pass this sure counts as a line change when learning (labels.CONFIDENT). */
export const CONFIDENT = 0.7;
/** Words either side of the playhead in the transcript strip. */
const CONTEXT_WORDS = 10;

function confTone(c: number): string {
  return c >= CONFIDENT
    ? "bg-emerald-400/80 text-black"
    : c >= 0.5
      ? "bg-amber-400/80 text-black"
      : "bg-zinc-500/60 text-zinc-100";
}

export function LookbackPassLane({
  items,
  view,
}: {
  items: LookbackItemView[];
  view: ViewWindow;
}) {
  return (
    <div className="absolute inset-x-0 top-0 h-16">
      {items.flatMap((item) =>
        item.passes.map((p, i) => {
          const lo = timeToViewFraction(p.t_start, view);
          const hi = timeToViewFraction(Math.max(p.t_end, p.t_start + 0.4), view);
          if (hi < -0.02 || lo > 1.02) return null;
          return (
            <div
              key={`lb-${item.item_idx}-${i}`}
              className="absolute top-1"
              style={{ left: `${lo * 100}%`, width: `${Math.max(0.2, (hi - lo) * 100)}%` }}
              title={
                `look-back → slide ${p.slide + 1} @ ${formatTime(p.t_start)}` +
                ` · ${Math.round(p.confidence * 100)}% · ${p.n_words} words` +
                (p.golden_slide == null ? "" : ` · golden ${p.golden_slide + 1}`)
              }
            >
              <div
                className={cn(
                  "h-5 overflow-hidden rounded-sm px-1 text-[10px] font-medium leading-5",
                  confTone(p.confidence),
                  p.agrees === false && "ring-2 ring-rose-500",
                )}
              >
                {p.slide + 1}
              </div>
              <div
                className={cn(
                  "h-9 border-l",
                  p.agrees === false ? "border-rose-500/70" : "border-white/20",
                )}
              />
            </div>
          );
        }),
      )}
    </div>
  );
}

interface PlacedWord extends LookbackWordView {
  item_idx: number;
}

export function LookbackStatePanel({
  playheadRef,
  items,
  golden,
  setlistContent,
  source,
  sourcesAvailable,
  goldenKind,
}: {
  playheadRef: RefObject<number>;
  items: LookbackItemView[];
  golden: GroundTruthEvent[];
  setlistContent: SetlistItemSnapshot[];
  source: string;
  sourcesAvailable: string[];
  goldenKind: string;
}) {
  const t = useFollowedTime(playheadRef);
  const words = useMemo<PlacedWord[]>(
    () =>
      items
        .flatMap((it) => it.words.map((w) => ({ ...w, item_idx: it.item_idx })))
        .sort((a, b) => a.t - b.t),
    [items],
  );
  const ordered = useMemo(
    () => [...golden].sort((a, b) => a.t_seconds - b.t_seconds),
    [golden],
  );

  let k = -1;
  for (const [i, w] of words.entries()) {
    if (w.t <= t) k = i;
    else break;
  }
  const word = k >= 0 ? (words[k] ?? null) : null;
  const item = word ? items.find((it) => it.item_idx === word.item_idx) : items[0];
  const pass = item
    ? [...item.passes].reverse().find((p) => p.t_start <= t) ?? null
    : null;
  let goldenAt: GroundTruthEvent | null = null;
  for (const g of ordered) {
    if (g.t_seconds <= t) goldenAt = g;
    else break;
  }
  const slides = setlistContent.find((s) => s.item_idx === word?.item_idx)?.slides ?? [];
  const line = word ? slides[word.slide] : undefined;
  const expected = word ? line?.[word.pos] : undefined;
  const context = k >= 0 ? words.slice(Math.max(0, k - CONTEXT_WORDS), k + CONTEXT_WORDS + 1) : [];

  if (items.length === 0) {
    return (
      <div className="w-80 shrink-0 rounded-md border border-dashed border-border p-4 text-xs text-muted-foreground">
        No look-back yet. It needs heard words; pick a source and hit Re-run.
      </div>
    );
  }

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto rounded-md border border-border p-4 text-xs">
      <div>
        <div className="uppercase tracking-wide text-muted-foreground">Look-back at playhead</div>
        <div className="mt-1 flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums">
            {pass ? pass.slide + 1 : "—"}
          </span>
          <span className="text-muted-foreground">
            golden {goldenAt ? goldenAt.slide_idx + 1 : "—"}
          </span>
        </div>
        {pass && (
          <>
            <div
              className={cn(
                "mt-1 font-medium",
                pass.agrees == null
                  ? "text-muted-foreground"
                  : pass.agrees
                    ? "text-emerald-400"
                    : "text-destructive",
              )}
            >
              {pass.agrees == null ? "no golden here" : pass.agrees ? "matches golden" : "disagrees with golden"}
            </div>
            <Meter
              label={`pass ${formatTime(pass.t_start)}–${formatTime(pass.t_end)} · ${pass.n_words} words`}
              value={pass.confidence}
            />
          </>
        )}
      </div>

      {word && (
        <div>
          <div className="mb-1 uppercase tracking-wide text-muted-foreground">Nearest word</div>
          <div>
            heard <span className="font-mono text-foreground">“{word.text}”</span> @ {word.t.toFixed(1)} s
          </div>
          <div className="text-muted-foreground">
            placed on slide {word.slide + 1}, word {word.pos + 1}
            {expected ? (
              <>
                {" "}— <span className="font-mono text-foreground">“{expected}”</span>
              </>
            ) : null}
          </div>
          <div className="mt-1 space-y-1">
            {word.top.map((s, i) => (
              <div key={s.slide} className="flex items-center gap-2">
                <span className="w-8 font-mono tabular-nums">{s.slide + 1}</span>
                <div className="h-1 flex-1 overflow-hidden rounded bg-muted">
                  <div
                    className={cn("h-full", i === 0 ? "bg-indigo-400" : "bg-indigo-400/40")}
                    style={{ width: `${s.p * 100}%` }}
                  />
                </div>
                <span className="w-10 text-right font-mono tabular-nums text-muted-foreground">
                  {s.p.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {context.length > 0 && (
        <div>
          <div className="mb-1 uppercase tracking-wide text-muted-foreground">Heard around here</div>
          <div className="leading-relaxed">
            {context.map((w, i) => (
              <span
                key={`${w.t}-${i}`}
                title={`${w.t.toFixed(1)} s · slide ${w.slide + 1} word ${w.pos + 1} · ${Math.round(w.p * 100)}%`}
                className={cn(
                  "mr-1",
                  w === word && "rounded bg-indigo-500/30 px-0.5 font-semibold text-foreground",
                  w.p < 0.5 ? "text-zinc-500" : w.p < CONFIDENT ? "text-amber-300" : "",
                )}
              >
                {w.text}
              </span>
            ))}
          </div>
        </div>
      )}

      {word && line && (
        <div>
          <div className="mb-1 uppercase tracking-wide text-muted-foreground">
            Slide {word.slide + 1} text
          </div>
          <div className="text-muted-foreground">
            {line.map((w, i) => (
              <span key={i} className={cn("mr-1", i === word.pos && "font-semibold text-foreground underline")}>
                {w}
              </span>
            ))}
          </div>
        </div>
      )}

      {item && (
        <Scorecard
          item={item}
          source={source}
          sourcesAvailable={sourcesAvailable}
          goldenKind={goldenKind}
        />
      )}
    </div>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="mt-1">
      <div className="flex justify-between text-muted-foreground">
        <span>{label}</span>
        <span className="font-mono tabular-nums">{Math.round(value * 100)}%</span>
      </div>
      <div className="h-1 overflow-hidden rounded bg-muted">
        <div
          className={cn("h-full", value >= CONFIDENT ? "bg-emerald-400" : value >= 0.5 ? "bg-amber-400" : "bg-zinc-500")}
          style={{ width: `${value * 100}%` }}
        />
      </div>
    </div>
  );
}

const GOLDEN_KIND: Record<string, string> = {
  approved: "approved golden",
  estimated: "unapproved golden (machine-estimated)",
  shown: "what was shown live (no golden)",
};

function Scorecard({
  item,
  source,
  sourcesAvailable,
  goldenKind,
}: {
  item: LookbackItemView;
  source: string;
  sourcesAvailable: string[];
  goldenKind: string;
}) {
  const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
  const seq = (xs: number[]) => xs.map((x) => x + 1).join(" ");
  return (
    <div className="space-y-1 rounded border border-border p-2">
      <div className="uppercase tracking-wide text-muted-foreground">
        {item.title || `Song ${item.item_idx + 1}`}
      </div>
      <div className={goldenKind === "approved" ? "text-muted-foreground" : "text-amber-400"}>
        judged against {GOLDEN_KIND[goldenKind] ?? goldenKind}
        {goldenKind === "shown" && " — if the AI drove, late and wrong slides here are the reference's, not the look-back's"}
      </div>
      <Row label="words from" value={`${source}${sourcesAvailable.length ? ` (have: ${sourcesAvailable.join(", ")})` : ""}`} />
      <Row label="heard words" value={String(item.heard_words)} />
      <Row label="precision" value={pct(item.precision)} />
      <Row label="recall" value={pct(item.recall)} />
      <Row
        label="line starts vs golden"
        value={item.median_offset_s == null ? "—" : `${item.median_offset_s >= 0 ? "+" : ""}${item.median_offset_s.toFixed(2)} s`}
      />
      <Row label="mean confidence" value={pct(item.mean_confidence)} />
      <div className="pt-1 text-muted-foreground">As a live session would store it:</div>
      <Row label="led by" value={item.led_by} />
      <Row label="operator agrees" value={pct(item.agreement)} />
      <Row label="weight" value={item.weight.toFixed(1)} />
      <Row label="look-back" value={`${item.lookback_ms.toFixed(0)} ms`} />
      <div className="pt-1">
        <div className="text-muted-foreground">confident lines</div>
        <div className="font-mono">{seq(item.lookback_sequence) || "—"}</div>
        <div className="text-muted-foreground">golden</div>
        <div className="font-mono">{seq(item.golden_sequence) || "—"}</div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate font-mono tabular-nums">{value}</span>
    </div>
  );
}
