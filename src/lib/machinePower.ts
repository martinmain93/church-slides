/**
 * Can this computer run live AI on its own, or should it use cloud speech?
 *
 * Read off what the engine reports about the machine (`recommend_model`) and
 * judged against what the build's performance runs measured
 * (scripts/perf_report.py, data/fixtures/perf/expected.json), following a
 * real song at real speed:
 *
 *   - Apple silicon on macOS 14+, speech on the graphics chip (MLX): about
 *     12% of one core, nothing dropped. Plenty of headroom.
 *   - The same M1 with speech on the processor (macOS 11-13, or no MLX):
 *     about 1.7 cores busy, and it still dropped audio now and then.
 *   - A 2-core PC with the smallest model: its slowest speech passes took the
 *     whole 1-second budget, so any other load makes it fall behind.
 *
 * Cloud speech (Groq) does the recognition on Groq's servers, so none of
 * this applies to it; it needs the internet for the whole service instead.
 */

import { loadAudioInputPrefs } from "@/lib/audioInputPrefs";
import { useModelRecommendation } from "@/lib/modelRecommendation";
import type { RecommendModelResponse } from "@/types/generated";

export type MachinePowerLevel = "good" | "limited" | "weak";

export interface MachinePower {
  level: MachinePowerLevel;
  /** What the operator should know, in their terms. */
  summary: string;
}

/** At or below this many cores the processor path is at its limit. */
const WEAK_CORES = 4;
/** Below this the speech model and the app compete for memory. */
const WEAK_RAM_GB = 8;

export function assessMachine(rec: RecommendModelResponse): MachinePower {
  if (rec.backend === "mlx") {
    return {
      level: "good",
      summary: "Fast enough: speech recognition runs on this Mac's graphics chip.",
    };
  }
  const fewCores = rec.cpu_count <= WEAK_CORES;
  const lowMemory = rec.total_ram_gb != null && rec.total_ram_gb < WEAK_RAM_GB;
  // Apple silicon that isn't using its graphics chip: on macOS 13 or older
  // (MLX needs 14). Updating macOS is the free fix.
  const appleSiliconOnCpu = rec.has_mps;
  if (fewCores || lowMemory) {
    const why = [
      fewCores ? `${rec.cpu_count} processor cores` : null,
      lowMemory ? `${Math.round(rec.total_ram_gb ?? 0)} GB of memory` : null,
    ]
      .filter(Boolean)
      .join(" and ");
    return {
      level: "weak",
      summary:
        `This computer (${why}) is likely to fall behind the band with speech recognition on ` +
        "its own processor, so slides would change late or not at all. Use Groq's online speech instead.",
    };
  }
  return {
    level: "limited",
    summary: appleSiliconOnCpu
      ? "Speech recognition runs on this Mac's processor, which keeps up but with little to spare. " +
        "Updating to macOS 14 or later lets it use the graphics chip, which is much faster; " +
        "until then, Groq's online speech is the dependable choice."
      : "Speech recognition runs on this computer's processor, which keeps up but with little " +
        "to spare: other busy apps can make slides late. Groq's online speech is the dependable choice.",
  };
}

/**
 * This computer's assessment, or null when it doesn't apply (cloud speech
 * chosen) or isn't known yet (the engine hasn't answered).
 */
export function useMachinePower(): MachinePower | null {
  const rec = useModelRecommendation();
  if (loadAudioInputPrefs().asr_backend === "groq") return null;
  return rec.kind === "ready" ? assessMachine(rec.recommendation) : null;
}
