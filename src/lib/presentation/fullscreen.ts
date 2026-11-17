/**
 * Fullscreen adapter. Uses Tauri's window API when available, falls back
 * to the standard browser Fullscreen API otherwise.
 *
 * The Tauri import is dynamic so the browser dev preview never tries to
 * resolve `@tauri-apps/api/webviewWindow` (which throws outside Tauri).
 */

import { isInTauri } from "@/lib/invoke";

// Safari before 16.4 only has the `webkit`-prefixed Fullscreen API. The Tauri
// path doesn't use it, but band screens run in whatever browser a phone or
// tablet has.
type WebkitDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};
type WebkitElement = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void };

function fullscreenElement(): Element | null {
  const doc = document as WebkitDocument;
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
}

export async function enterFullscreen(): Promise<void> {
  if (isInTauri()) {
    const { getCurrentWebviewWindow } = await import(
      "@tauri-apps/api/webviewWindow"
    );
    await getCurrentWebviewWindow().setFullscreen(true);
    return;
  }
  if (fullscreenElement()) return;
  const el = document.documentElement as WebkitElement;
  if (el.requestFullscreen) await el.requestFullscreen();
  else await el.webkitRequestFullscreen?.();
}

export async function exitFullscreen(): Promise<void> {
  if (isInTauri()) {
    const { getCurrentWebviewWindow } = await import(
      "@tauri-apps/api/webviewWindow"
    );
    await getCurrentWebviewWindow().setFullscreen(false);
    return;
  }
  if (fullscreenElement()) {
    const doc = document as WebkitDocument;
    if (doc.exitFullscreen) await doc.exitFullscreen();
    else await doc.webkitExitFullscreen?.();
  }
}

export async function toggleFullscreen(): Promise<void> {
  if (isInTauri()) {
    const { getCurrentWebviewWindow } = await import(
      "@tauri-apps/api/webviewWindow"
    );
    const win = getCurrentWebviewWindow();
    const on = await win.isFullscreen();
    await win.setFullscreen(!on);
    return;
  }
  if (fullscreenElement()) {
    await exitFullscreen();
  } else {
    await enterFullscreen();
  }
}

export function isFullscreen(): boolean {
  return Boolean(fullscreenElement());
}
