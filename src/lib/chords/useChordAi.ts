/**
 * Whether the song editor's AI helpers (Voice, and model-backed next-chord
 * suggestions) are shown at all.
 *
 * Both call Groq with the key saved in Settings → AI, so with no key, or
 * with the AI switched off, they're hidden rather than shown and failing
 * ("hide it until it's used"). The local Suggest rules need neither and
 * stay.
 */

import { useEffect } from "react";

import { useAiEnabled } from "@/lib/aiPrefs";
import { refreshInstructionModels, useInstructionModels } from "@/lib/instructionModels";

export function useChordAi(): boolean {
  const aiEnabled = useAiEnabled();
  const models = useInstructionModels();

  useEffect(() => {
    if (aiEnabled && !models.seen) refreshInstructionModels().catch(() => undefined);
  }, [aiEnabled, models.seen]);

  return aiEnabled && models.models.some((m) => m.provider === "groq" && m.has_api_key);
}
