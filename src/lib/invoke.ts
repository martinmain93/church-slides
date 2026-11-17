/**
 * Dispatcher for Tauri commands.
 *
 * Inside the Tauri shell, calls go to the real Rust backend via
 * `@tauri-apps/api/core`'s `invoke`. In a plain browser (e.g. running
 * `pnpm dev` for UI iteration), calls are routed to in-memory mocks
 * defined in `src/lib/mockTauri.ts`.
 *
 * Detection uses Tauri's runtime marker `__TAURI_INTERNALS__`, which is
 * set by the Tauri shell before our React code mounts. The mocks load only
 * in dev and tests: `import.meta.env.DEV` is `false` in a production build,
 * so the branch and the dynamic import drop out and the mocks never reach
 * the bundle. A production page outside Tauri (a band screen on a phone)
 * gets an error rather than mock data.
 */

import { invoke as tauriInvoke } from "@tauri-apps/api/core";

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isInTauri(): boolean {
  return typeof window !== "undefined" && window.__TAURI_INTERNALS__ !== undefined;
}

export async function invoke<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  if (isInTauri()) {
    return tauriInvoke<T>(command, args);
  }
  if (!import.meta.env.DEV) {
    throw new Error(`${command} needs the desktop app`);
  }
  const { mockTauri } = await import("@/lib/mockTauri");
  if (command in mockTauri) {
    const fn = mockTauri[command as keyof typeof mockTauri] as (
      args?: Record<string, unknown>,
    ) => Promise<T>;
    return fn(args ?? {});
  }
  throw new Error(`unimplemented mock command: ${command}`);
}
