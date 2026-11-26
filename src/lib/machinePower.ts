/**
 * Can this computer run live AI? Only Apple silicon on macOS 14 or later can
 * (2026-11-23): the on-device speech engine runs on its graphics chip, and
 * there is no other path (the processor path and Groq's hosted speech are no
 * longer offered). The numbers that led here:
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
 */

import { useModelRecommendation } from "@/lib/modelRecommendation";
import type { RecommendModelResponse } from "@/types/generated";

export type MachinePowerLevel = "good" | "weak";

export interface MachinePower {
  level: MachinePowerLevel;
  /** What the operator should know, in their terms. */
  summary: string;
}

export function assessMachine(rec: RecommendModelResponse): MachinePower {
  if (rec.backend === "mlx") {
    return {
      level: "good",
      summary: "Fast enough: speech recognition runs on this Mac's graphics chip.",
    };
  }
  // Apple silicon that isn't using its graphics chip: on macOS 13 or older
  // (MLX needs 14). Updating macOS is the free fix.
  if (rec.has_mps) {
    return {
      level: "weak",
      summary:
        "Live AI needs macOS 14 or later on this Mac. Updating macOS turns it on; until " +
        "then, present by hand.",
    };
  }
  return {
    level: "weak",
    summary:
      "Live AI needs a Mac with Apple silicon (M1 or newer). On this computer, present by hand.",
  };
}

/** This computer's assessment, or null while the engine hasn't answered. */
export function useMachinePower(): MachinePower | null {
  const rec = useModelRecommendation();
  return rec.kind === "ready" ? assessMachine(rec.recommendation) : null;
}
