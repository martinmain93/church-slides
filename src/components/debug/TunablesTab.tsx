/**
 * Live-editable engine parameters (the scheduler's two knobs).
 *
 * Each change persists to localStorage (so it survives reloads) and
 * fires `set_tunables` to the running sidecar (so the matcher picks it
 * up immediately). Other connected clients receive a
 * `tunables_updated` broadcast and re-render to match.
 *
 * The fields mirror `ParticleFilterTunables` from the protocol (a historical
 * name; the particle filter's own knobs went with it).
 *
 * **A knob's range must contain its own default.** The old `lead_time_ms` was
 * capped at 1000 while its default was 1500, and a range input silently
 * clamps: the slider showed the cap, and the first touch of *any* knob pushed
 * the clamped 1000 to the sidecar and persisted it. The engine then ran a lead
 * time nobody had chosen and the panel reported it as if it were configured. A
 * test in `__tests__/TunablesTab.test.tsx` holds the invariant for every knob.
 *
 * That knob is gone. "Slide timing" replaces it, and is a preference rather
 * than a mechanism: the lead that lands a slide on the beat is the measured
 * delivery lag plus the gap to the next belief update plus this, and the
 * engine derives the first two continuously. Centred on 0 — aim at the line —
 * with two seconds of adjustment either way.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { setAiMode, useAiStatus } from "@/lib/aiDecisions";
import { loadAiPrefs, saveAiPrefs, type AIOnMode } from "@/lib/aiPrefs";
import {
  DEFAULT_TUNABLES,
  pushTunables,
  useTunables,
} from "@/lib/tunables";
import type { ParticleFilterTunables } from "@/types/generated";

interface Knob {
  key: keyof ParticleFilterTunables;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
}

export const KNOBS: Knob[] = [
  {
    key: "target_margin_ms",
    label: "Slide timing",
    hint:
      "Negative puts the slide up after the line, positive before it. " +
      "The engine adds the measured transcript lag itself.",
    min: -2000,
    max: 2000,
    step: 100,
  },
  {
    key: "marginal_threshold",
    label: "Marginal threshold",
    hint: "Mode marginal must exceed this before the scheduler commits.",
    min: 0,
    max: 1,
    step: 0.05,
  },
];


export function TunablesTab() {
  const remote = useTunables();
  const [local, setLocal] = useState<ParticleFilterTunables>(remote);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Keep `local` in sync when a server-side broadcast arrives.
  useEffect(() => {
    setLocal(remote);
  }, [remote]);

  async function update(key: keyof ParticleFilterTunables, value: number) {
    const next = { ...local, [key]: value };
    setLocal(next);
    setErr(null);
    setBusy(true);
    try {
      await pushTunables(next);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setLocal(DEFAULT_TUNABLES);
    setErr(null);
    setBusy(true);
    try {
      await pushTunables(DEFAULT_TUNABLES);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col gap-3 px-3 py-3 text-xs">
      <OnModePicker />
      <div className="flex items-center justify-between">
        <span className="text-zinc-400">Scheduler</span>
        <Button type="button" size="sm" variant="ghost" onClick={reset} disabled={busy}>
          Reset to defaults
        </Button>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto pr-1">
        {KNOBS.map((knob) => (
          <KnobRow
            key={knob.key}
            knob={knob}
            value={local[knob.key] as number}
            onChange={(v) => update(knob.key, v)}
            disabled={busy}
          />
        ))}
      </div>

      {err && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-200">
          {err}
        </div>
      )}
    </div>
  );
}


/**
 * What switching the AI on means: Active (moves slides) or Shadow (follows
 * and logs only). A developer tool for validating the matcher — operators
 * just switch the AI on and off from the status pill.
 */
function OnModePicker() {
  const status = useAiStatus();
  const [onMode, setOnMode] = useState<AIOnMode>(() => loadAiPrefs().onMode);
  async function pick(next: AIOnMode) {
    setOnMode(next);
    saveAiPrefs({ onMode: next });
    // A running AI switches over now; one that's off stays off.
    if (status && status.mode !== "off") await setAiMode(next).catch(() => undefined);
  }
  return (
    <div className="flex items-center justify-between gap-2 rounded border border-white/10 bg-white/5 px-2 py-1.5">
      <span className="text-zinc-300">When the AI is on</span>
      <div className="flex gap-1" role="radiogroup" aria-label="When the AI is on">
        {(["active", "shadow"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={onMode === m}
            onClick={() => void pick(m)}
            title={m === "active" ? "It moves the slides" : "It follows and logs, but never moves the slides"}
            className={
              onMode === m
                ? "rounded bg-emerald-500/20 px-2 py-0.5 text-emerald-200"
                : "rounded px-2 py-0.5 text-zinc-400 hover:text-zinc-200"
            }
          >
            {m === "active" ? "Active" : "Shadow"}
          </button>
        ))}
      </div>
    </div>
  );
}

function KnobRow({
  knob,
  value,
  onChange,
  disabled,
}: {
  knob: Knob;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <label htmlFor={knob.key} className="font-medium text-zinc-200">
          {knob.label}
        </label>
        <span className="tabular-nums text-zinc-400">
          {formatValue(knob.step, value)}
        </span>
      </div>
      <input
        id={knob.key}
        type="range"
        min={knob.min}
        max={knob.max}
        step={knob.step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
      <div className="text-[10px] text-zinc-500">{knob.hint}</div>
    </div>
  );
}


function formatValue(step: number, v: number): string {
  if (step >= 1) return String(Math.round(v));
  const decimals = step < 0.01 ? 3 : step < 0.1 ? 2 : 1;
  return v.toFixed(decimals);
}
