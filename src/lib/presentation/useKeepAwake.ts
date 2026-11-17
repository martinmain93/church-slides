/**
 * Keep the display awake for as long as a presentation view is mounted.
 *
 * With nobody at the laptop — the worship leader driving from their own
 * device — macOS would otherwise dim and sleep the display (and the projector
 * with it) after its idle timeout. No-op outside Tauri; a failure is logged,
 * not surfaced, since presenting works either way.
 */

import { useEffect } from "react";

import { isInTauri } from "@/lib/invoke";
import { setKeepAwake } from "@/lib/tauri";

export function useKeepAwake(): void {
  useEffect(() => {
    if (!isInTauri()) return;
    setKeepAwake(true).catch((err) => console.warn("keep-awake failed:", err));
    return () => {
      setKeepAwake(false).catch(() => undefined);
    };
  }, []);
}
