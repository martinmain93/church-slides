/**
 * Horizontal RMS dBFS meter with a separate peak indicator.
 *
 * Renders nothing dramatic — a thin filled bar that maps -60 dBFS → 0%
 * and 0 dBFS → 100%, plus a single notch for the absolute peak. The
 * scale is fixed so the eye can compare relative loudness across
 * sessions without recalibrating.
 */

import { cn } from "@/lib/utils";

export interface VuMeterProps {
  rmsDbfs: number | null;
  peak: number;
  /** Display floor in dBFS — readings below this clamp to 0%. */
  floorDb?: number;
  className?: string;
}

export function VuMeter({
  rmsDbfs,
  peak,
  floorDb = -60,
  className,
}: VuMeterProps) {
  const rmsFraction =
    rmsDbfs === null
      ? 0
      : Math.max(0, Math.min(1, (rmsDbfs - floorDb) / -floorDb));

  // Peak amplitude is already in [0, 1]; map to the same display scale
  // by converting to dBFS first so the notch lines up with the bar.
  const peakDb = peak > 0 ? 20 * Math.log10(peak) : floorDb;
  const peakFraction = Math.max(0, Math.min(1, (peakDb - floorDb) / -floorDb));

  // Color tiers: green up to -12 dB, amber up to -3, red above.
  const barColor =
    rmsDbfs === null
      ? "bg-zinc-700"
      : rmsDbfs > -3
      ? "bg-red-500"
      : rmsDbfs > -12
      ? "bg-amber-400"
      : "bg-emerald-500";

  return (
    <div
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-zinc-800",
        className,
      )}
      role="meter"
      aria-label="Audio input level"
      aria-valuemin={floorDb}
      aria-valuemax={0}
      aria-valuenow={rmsDbfs ?? floorDb}
    >
      <div
        className={cn(
          "absolute inset-y-0 left-0 transition-[width] duration-75",
          barColor,
        )}
        style={{ width: `${rmsFraction * 100}%` }}
      />
      {peak > 0 && (
        <div
          className="absolute inset-y-0 w-px bg-white/80"
          style={{ left: `${peakFraction * 100}%` }}
        />
      )}
    </div>
  );
}
