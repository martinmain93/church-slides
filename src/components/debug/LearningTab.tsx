/**
 * What the app is learning about how this church plays its songs — the only
 * place it shows (Plan/LEARNED_REFERENCES.md).
 *
 * Per setlist song: the look-back of this service (where the band was, in
 * hindsight, from the words), what the operator showed, the past services
 * on record and how much each counts, and the order those services taught
 * against library order. Polls the sidecar while open; nothing here changes
 * what the screen shows except forgetting a past service.
 */

import { Fragment, useCallback, useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import { cn } from "@/lib/utils";
import type {
  GetLearningStateResponse,
  LearnedSongView,
  LearnedTransitionView,
  PerformanceView,
} from "@/types/generated";

const POLL_MS = 3000;
/** A look-back pass this sure of its line counts as a line change (labels.CONFIDENT). */
const CONFIDENT = 0.7;

export function LearningTab() {
  const [state, setState] = useState<GetLearningStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    requestSidecar({ kind: "get_learning_state" })
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const forget = (id: string) => {
    requestSidecar({ kind: "delete_performance", performance_id: id })
      .then(refresh)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  if (error) {
    return <p className="px-4 py-3 text-xs text-rose-300">Learning state unavailable: {error}</p>;
  }
  if (!state) {
    return <p className="px-4 py-3 text-xs text-zinc-500">Loading…</p>;
  }

  return (
    <div className="space-y-3 px-4 py-3 text-xs">
      <p className="text-zinc-400">
        {state.collecting ? (
          <>
            <span className="text-emerald-300">Collecting</span>
            {state.provenance !== "live" && (
              <span className="text-amber-300"> ({state.provenance}: kept, never learned from)</span>
            )}
            {" · "}audio {state.audio_now.toFixed(0)} s · {state.passes_run} look-backs
          </>
        ) : (
          "Not collecting (no session with a setlist)."
        )}
      </p>
      {state.songs.length === 0 && (
        <p className="text-zinc-500">Start a session with a setlist to see what has been learned.</p>
      )}
      {state.songs.map((song) => (
        <SongCard key={`${song.item_idx}-${song.song_id}`} song={song} onForget={forget} />
      ))}
      {state.store && <p className="truncate text-[10px] text-zinc-600">{state.store}</p>}
    </div>
  );
}

function SongCard({ song, onForget }: { song: LearnedSongView; onForget: (id: string) => void }) {
  return (
    <section className="rounded border border-white/10 bg-white/5 px-3 py-2">
      <header className="mb-1 flex items-baseline justify-between gap-2">
        <span className="truncate font-medium text-zinc-100">
          {song.item_idx + 1}. {song.title || song.song_id}
        </span>
        <span className="whitespace-nowrap text-[10px] text-zinc-400">
          {song.follower_source ? `follower: ${song.follower_source.replace("_", " ")}` : "matcher"}
        </span>
      </header>
      <p className="text-[11px] text-zinc-400">
        {song.used} past service{song.used === 1 ? "" : "s"} (weight {song.n_eff.toFixed(1)}) ·{" "}
        {song.timed_lines} lines timed
        {song.seconds_per_word != null && ` · usually ${song.seconds_per_word.toFixed(2)} s/word`}
      </p>

      <h4 className="mt-2 text-[10px] uppercase tracking-wide text-zinc-500">This service</h4>
      {song.live ? (
        <>
          <Passes perf={song.live} />
          <Meta perf={song.live} extra={`${song.live_heard_words} words · look-back ${song.lookback_ms.toFixed(0)} ms`} />
        </>
      ) : (
        <p className="text-zinc-500">
          {song.live_heard_words > 0 ? `${song.live_heard_words} words heard so far` : "Not sung yet."}
        </p>
      )}

      <OrderSection song={song} />

      <h4 className="mt-2 text-[10px] uppercase tracking-wide text-zinc-500">Past services</h4>
      {song.performances.length === 0 && <p className="text-zinc-500">None yet.</p>}
      <ul className="space-y-1">
        {song.performances.map((p) => (
          <li key={p.id} className="rounded bg-black/20 px-2 py-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-zinc-300">{new Date(p.started_at_ms).toLocaleString()}</span>
              <button
                type="button"
                title="Forget this service"
                onClick={() => onForget(p.id)}
                className="text-zinc-500 hover:text-rose-300"
              >
                forget
              </button>
            </div>
            <Passes perf={p} />
            <Meta perf={p} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Line by line: where library order goes next and where services went. A row
 * opens to show where that comes from — each service's line change, how sure
 * the look-back was of both lines, whether the operator made the same move —
 * and what it does to the follower's odds for the next line.
 */
function OrderSection({ song }: { song: LearnedSongView }) {
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const learned = song.order.filter((t) => t.successors.length > 0);
  const rows = showAll ? song.order : learned;
  const text = (slide: number | null | undefined) =>
    slide == null ? "" : (song.slide_text[slide] ?? "").split(" ").slice(0, 6).join(" ");
  return (
    <>
      <h4 className="mt-2 flex items-baseline justify-between text-[10px] uppercase tracking-wide text-zinc-500">
        <span>
          Learned order{" "}
          {learned.length === 0 && <span className="normal-case text-zinc-600">(nothing yet)</span>}
        </span>
        <button
          type="button"
          className="normal-case text-zinc-500 hover:text-zinc-300"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? "learned lines only" : "all lines"}
        </button>
      </h4>
      {rows.length > 0 && (
        <table className="w-full text-[11px]">
          <thead className="text-zinc-500">
            <tr>
              <th className="text-left font-normal">after line</th>
              <th className="text-left font-normal">library</th>
              <th className="text-left font-normal">services</th>
              <th className="text-right font-normal">say</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const differs = t.learned_next != null && t.learned_next !== t.library_next;
              const isOpen = open === t.from_slide;
              return (
                <Fragment key={t.from_slide}>
                  <tr
                    className={cn(
                      "cursor-pointer text-zinc-300 hover:bg-white/5",
                      isOpen && "bg-white/5",
                    )}
                    onClick={() => setOpen(isOpen ? null : t.from_slide)}
                    title={song.slide_text[t.from_slide]}
                  >
                    <td>
                      <span className="text-zinc-500">{isOpen ? "▾" : "▸"}</span> {t.from_slide}
                    </td>
                    <td className="text-zinc-500">{t.library_next ?? "—"}</td>
                    <td className={differs ? "text-amber-300" : "text-zinc-300"}>
                      {t.learned_next ?? "—"}
                      {t.learned_next != null && (
                        <span className="text-zinc-500"> ×{t.count.toFixed(1)}</span>
                      )}
                    </td>
                    <td className="text-right">{t.share ? `${Math.round(t.share * 100)}%` : ""}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={4} className="pb-2 pt-1">
                        <TransitionDetail t={t} text={text} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

function TransitionDetail({
  t,
  text,
}: {
  t: LearnedTransitionView;
  text: (slide: number | null | undefined) => string;
}) {
  const base = new Map(t.base_prior.map((x) => [x.slide, x.p]));
  const slides = [...new Set([...t.prior.map((x) => x.slide), ...t.base_prior.map((x) => x.slide)])];
  const now = new Map(t.prior.map((x) => [x.slide, x.p]));
  return (
    <div className="space-y-2 rounded bg-black/30 px-2 py-1.5 text-[10px] text-zinc-400">
      <p className="text-zinc-300">
        Line {t.from_slide}: <span className="italic text-zinc-400">{text(t.from_slide)}…</span>
      </p>
      <div>
        <p className="mb-0.5 uppercase tracking-wide text-zinc-500">Follower expects next</p>
        {slides.length === 0 && <p>No order prior for this line.</p>}
        {slides.map((sl) => {
          const p = now.get(sl) ?? 0;
          const was = base.get(sl) ?? 0;
          return (
            <div key={sl} className="flex items-center gap-2" title={text(sl)}>
              <span className="w-6 text-right text-zinc-300">{sl}</span>
              <div className="relative h-1.5 flex-1 rounded bg-white/5">
                <div className="absolute inset-y-0 left-0 rounded bg-zinc-500/50" style={{ width: `${was * 100}%` }} />
                <div className="absolute inset-y-0 left-0 rounded bg-emerald-400/70" style={{ width: `${p * 100}%` }} />
              </div>
              <span className="w-20 text-right tabular-nums">
                {Math.round(p * 100)}%
                {Math.abs(p - was) >= 0.01 && <span className="text-zinc-500"> (was {Math.round(was * 100)}%)</span>}
              </span>
            </div>
          );
        })}
      </div>
      {t.successors.length > 0 && (
        <p>
          Services went to{" "}
          {t.successors.map((x, i) => (
            <span key={x.slide} title={text(x.slide)}>
              {i > 0 && ", "}
              <span className="text-zinc-200">{x.slide}</span> ×{x.p.toFixed(1)}
            </span>
          ))}
          {" · "}learned say {Math.round(t.share * 100)}% of this line&apos;s next step
        </p>
      )}
      {t.evidence.length > 0 && (
        <div>
          <p className="mb-0.5 uppercase tracking-wide text-zinc-500">Evidence</p>
          <ul className="space-y-0.5">
            {t.evidence.map((ev, i) => (
              <li key={`${ev.performance_id}-${i}`} className="flex flex-wrap gap-x-2">
                <span className="text-zinc-300">→ {ev.to_slide}</span>
                <span>{new Date(ev.started_at_ms).toLocaleDateString()}</span>
                <span>at {ev.t.toFixed(0)} s</span>
                <span>led by {ev.led_by}</span>
                <span>counts {ev.weight.toFixed(2)}</span>
                <span title="look-back confidence in the line left → the line entered">
                  sure {Math.round(ev.conf_from * 100)}%→{Math.round(ev.conf_to * 100)}%
                </span>
                <span
                  className={
                    ev.operator_agrees == null
                      ? "text-zinc-500"
                      : ev.operator_agrees
                        ? "text-emerald-300"
                        : "text-rose-300"
                  }
                >
                  {ev.operator_agrees == null
                    ? "operator: no clicks"
                    : ev.operator_agrees
                      ? "operator made it too"
                      : "operator didn't"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The look-back's lines in order, coloured by how sure it is; the operator's below. */
function Passes({ perf }: { perf: PerformanceView }) {
  return (
    <div className="my-1 space-y-0.5">
      <div className="flex flex-wrap items-center gap-0.5">
        <span className="w-10 text-[10px] text-zinc-500">heard</span>
        {perf.passes.map((p, i) => (
          <span
            key={i}
            title={`${p.t_start.toFixed(1)} s · ${Math.round(p.confidence * 100)}%`}
            className={cn(
              "rounded px-1 text-[10px]",
              p.confidence >= CONFIDENT
                ? "bg-emerald-500/20 text-emerald-200"
                : p.confidence >= 0.5
                  ? "bg-amber-500/20 text-amber-200"
                  : "bg-white/5 text-zinc-500",
            )}
          >
            {p.slide}
          </span>
        ))}
      </div>
      {perf.operator.length > 0 && (
        <div className="flex flex-wrap items-center gap-0.5">
          <span className="w-10 text-[10px] text-zinc-500">shown</span>
          {perf.operator.map((m, i) => (
            <span
              key={i}
              title={`${m.t.toFixed(1)} s · ${m.source}`}
              className={cn(
                "rounded px-1 text-[10px]",
                m.source === "ai" ? "bg-sky-500/15 text-sky-300" : "bg-white/10 text-zinc-300",
              )}
            >
              {m.slide}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Meta({ perf, extra }: { perf: PerformanceView; extra?: string }) {
  return (
    <p className="text-[10px] text-zinc-500">
      led by {perf.led_by} · weight {perf.weight.toFixed(1)}
      {perf.agreement != null && ` · operator agrees ${Math.round(perf.agreement * 100)}%`}
      {perf.mean_confidence != null && ` · sure ${Math.round(perf.mean_confidence * 100)}%`}
      {perf.provenance !== "live" && <span className="text-amber-400"> · {perf.provenance}</span>}
      {!perf.final && " · draft"}
      {extra && ` · ${extra}`}
    </p>
  );
}
