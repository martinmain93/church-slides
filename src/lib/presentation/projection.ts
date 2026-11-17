/**
 * Second-screen projection adapter.
 *
 * The operator window owns the presentation state machine; the audience
 * window is a dumb renderer that mirrors whatever slide the operator emits.
 *
 * The operator view opens the audience window by itself when the service
 * starts. Rust looks for a monitor none of this app's windows is on and, if
 * there is one, puts the window there fullscreen; otherwise it opens as a
 * normal decorated window to drag across. Positioning is done once, at
 * creation, from Rust — the earlier "projection doesn't work" breakage came
 * from the JS window API, not from placing the window.
 *
 * Every Tauri API is imported dynamically (mirroring `fullscreen.ts`) so the
 * browser dev preview — which has no Tauri runtime — degrades to no-ops.
 */

import { invoke, isInTauri } from "@/lib/invoke";
import type { DisplayLook } from "@/lib/displayLook";
import type { ParsedSlide } from "@/lib/parseSong";
import type { FlowAnchor } from "@/lib/presentation/flow";
import type { BlankMode } from "@/lib/presentation/state";
import type { ScriptureSettings } from "@/lib/scripture/settings";
import type { ScripturePassage } from "@/types/generated";

/** Event carrying the current slide from operator → audience. */
export const SLIDE_EVENT = "audience://slide";
/** Event the audience window emits on mount to request an immediate sync. */
export const REQUEST_STATE_EVENT = "audience://request-state";

/** Serializable snapshot of what the audience should render. */
export interface SlidePayload {
  slide: ParsedSlide | null;
  blankMode: BlankMode;
  /**
   * The passage to show alongside the lyrics, or null for none — which is
   * also what every payload carries while the scripture feature is off, and
   * what any payload sent by an older build carries. The audience window
   * treats a missing field exactly as it treats null, so the two windows can
   * disagree about this feature without breaking.
   */
  scripture?: ScripturePassage | null;
  /** Display settings, sent with the payload so the audience window needs no
   * settings access of its own. Absent = feature off. */
  scriptureSettings?: ScriptureSettings | null;
  /** Scripture takes the whole screen rather than a pane. */
  scriptureFullscreen?: boolean;
  /** Continuous mode: the song on screen and what its column follows.
   * Absent or null = slide mode, which is also what an older build sends. */
  flow?: FlowView | null;
  /** Colour scheme, typeface and size. Absent = the default look (white on
   * black), which is what an older build sends. */
  look?: DisplayLook | null;
}

/** Everything `FlowStage` needs, in a form that crosses to another window. */
export interface FlowView {
  itemIdx: number;
  slides: ParsedSlide[];
  anchor: FlowAnchor;
}

/** True if the audience window currently exists. */
export async function isAudienceOpen(): Promise<boolean> {
  if (!isInTauri()) return false;
  return invoke<boolean>("is_audience_window_open").catch(() => false);
}

/**
 * Open the audience window (or focus it if already open). Window creation runs
 * in Rust (`open_audience_window` command) — custom commands aren't ACL-gated,
 * which avoids the silent webview-creation failure the JS `WebviewWindow` API
 * hit. It loads the same bundle with `?window=audience`, which the app entry
 * point detects to render the bare audience surface.
 *
 * With `fillSpare`, a spare monitor gets it fullscreen. Resolves to whether
 * the window is fullscreen (false: it's a normal window to drag across).
 */
export async function openAudienceWindow(fillSpare = false): Promise<boolean> {
  if (!isInTauri()) return false;
  return invoke<boolean>("open_audience_window", { fillSpare });
}

/** How many displays are connected (1 outside Tauri). */
export async function monitorCount(): Promise<number> {
  if (!isInTauri()) return 1;
  return invoke<number>("monitor_count").catch(() => 1);
}

/** Close the audience window if it exists. */
export async function closeAudienceWindow(): Promise<void> {
  if (!isInTauri()) return;
  await invoke("close_audience_window").catch(() => undefined);
}

/** Toggle the audience window between fullscreen and windowed. */
export async function toggleAudienceFullscreen(): Promise<void> {
  if (!isInTauri()) return;
  await invoke("toggle_audience_fullscreen").catch(() => undefined);
}

/** Push the current slide to the audience window. No-op outside Tauri. */
export async function emitSlide(payload: SlidePayload): Promise<void> {
  if (!isInTauri()) return;
  const { emit } = await import("@tauri-apps/api/event");
  await emit(SLIDE_EVENT, payload).catch(() => undefined);
}

/**
 * Listen for the audience window's "I just mounted, send me the current
 * state" request. Returns an unlisten function (a no-op outside Tauri).
 */
export async function onAudienceRequest(
  handler: () => void,
): Promise<() => void> {
  if (!isInTauri()) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  const unlisten = await listen(REQUEST_STATE_EVENT, () => handler());
  return unlisten;
}

/** True if the detached debug window currently exists. */
export async function isDebugWindowOpen(): Promise<boolean> {
  if (!isInTauri()) return false;
  return invoke<boolean>("is_debug_window_open").catch(() => false);
}

/**
 * Open the debug panel as its own window, or focus it if already open.
 *
 * Same Rust-side construction as the audience window and for the same reason:
 * custom commands aren't ACL-gated, so this avoids the silent
 * webview-creation failure the JS `WebviewWindow` API hit.
 */
export async function openDebugWindow(): Promise<void> {
  if (!isInTauri()) return;
  await invoke("open_debug_window");
}

/** Close the detached debug window if it exists. */
export async function closeDebugWindow(): Promise<void> {
  if (!isInTauri()) return;
  await invoke("close_debug_window").catch(() => undefined);
}

/** A connected display, as the Screens dialog draws it (`list_screens`). */
export interface ScreenInfo {
  /** Name and size: how a display is recognised from one service to the next. */
  key: string;
  name: string;
  /** Logical position and size, for drawing the arrangement. */
  x: number;
  y: number;
  width: number;
  height: number;
  pixel_width: number;
  pixel_height: number;
  /** The operator's own display. */
  operator: boolean;
  /** Never picked automatically. */
  avoid: boolean;
  /** This app's output windows on it: `audience`, `band-1`, … */
  windows: string[];
}

/** Every connected display, in the order Identify numbers them. */
export async function listScreens(): Promise<ScreenInfo[]> {
  if (!isInTauri()) return [];
  return invoke<ScreenInfo[]>("list_screens");
}

/** Move the audience or a band screen window to a display, and remember it. */
export async function moveWindowToScreen(label: string, key: string): Promise<void> {
  if (!isInTauri()) return;
  await invoke("move_window_to_screen", { label, key });
}

/** Mark a display "Don't use" for automatic placement, or clear it. */
export async function setScreenAvoid(key: string, avoid: boolean): Promise<void> {
  if (!isInTauri()) return;
  await invoke("set_screen_avoid", { key, avoid });
}

/** Show each display's number on it for a few seconds. */
export async function identifyScreens(): Promise<void> {
  if (!isInTauri()) return;
  await invoke("identify_screens");
}
