/**
 * `useModelRecommendation()` — asks the sidecar for the recommended
 * Whisper model on this host. Cached for the lifetime of the hook
 * instance so the (potentially-slow) hardware probe runs once per
 * mount.
 */

import { useEffect, useState } from "react";

import { requestSidecar } from "@/lib/sidecarClient";
import type { RecommendModelResponse } from "@/types/generated";

export type ModelRecommendationState =
  | { kind: "loading" }
  | { kind: "ready"; recommendation: RecommendModelResponse }
  | { kind: "error"; message: string };

export function useModelRecommendation(): ModelRecommendationState {
  const [state, setState] = useState<ModelRecommendationState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    requestSidecar({ kind: "recommend_model" })
      .then((reply) => {
        if (cancelled) return;
        setState({ kind: "ready", recommendation: reply });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
