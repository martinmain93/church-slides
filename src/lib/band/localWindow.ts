/**
 * A band screen can also be a window on the operator's own computer (an extra
 * monitor for a team member without a tablet). Those windows are labelled
 * `band-1`, `band-2`, … by `open_band_window` in Rust.
 */

import { invoke } from "@/lib/invoke";

/** This window's label if it's a band screen on the operator's computer, else null. */
export function localBandWindowLabel(): string | null {
  const label = (
    window as unknown as { __TAURI_INTERNALS__?: { metadata?: { currentWindow?: { label?: string } } } }
  ).__TAURI_INTERNALS__?.metadata?.currentWindow?.label;
  return label?.startsWith("band-") ? label : null;
}

/** Toggle fullscreen for this window. Resolves to the new state. */
export function toggleOwnFullscreen(): Promise<boolean> {
  return invoke<boolean>("toggle_own_fullscreen");
}
