/**
 * The UI's errors and warnings, copied into the app's log file.
 *
 * Every failure the UI handles by logging — a session that wouldn't start, a
 * dropped engine connection, an uncaught exception — used to stay in the
 * webview's console, which nobody outside a dev build can open. Forwarded
 * through `log_from_ui`, they sit in the log next to the engine's own lines,
 * so a diagnostics file shows both sides of the same moment.
 *
 * Rate-limited: a render loop logging on every frame must not fill the log
 * (it rotates at 5 MB, and an hour of noise would push out the useful part).
 */

import { invoke, isInTauri } from "@/lib/invoke";

const MAX_PER_MINUTE = 30;
const MAX_CHARS = 4000;

let installed = false;
let windowStart = 0;
let sentThisWindow = 0;
let dropped = 0;

/** Which window this is — the audience, band and debug windows share the bundle. */
function windowName(): string {
  try {
    return new URLSearchParams(window.location.search).get("window") ?? "main";
  } catch {
    return "main";
  }
}

export function formatLogArgs(args: unknown[]): string {
  return args
    .map((a) => {
      // WebKit's `stack` is only the frames, without the message.
      if (a instanceof Error) return `${a.name}: ${a.message}${a.stack ? `\n${a.stack}` : ""}`;
      if (typeof a === "string") return a;
      try {
        return JSON.stringify(a);
      } catch {
        return String(a);
      }
    })
    .join(" ")
    .slice(0, MAX_CHARS);
}

function send(level: "error" | "warn" | "info", message: string) {
  const now = Date.now();
  if (now - windowStart > 60_000) {
    if (dropped > 0) {
      const note = `[${windowName()}] ${dropped} more UI messages dropped (rate limit)`;
      void invoke("log_from_ui", { level: "warn", message: note }).catch(() => undefined);
    }
    windowStart = now;
    sentThisWindow = 0;
    dropped = 0;
  }
  if (sentThisWindow >= MAX_PER_MINUTE) {
    dropped += 1;
    return;
  }
  sentThisWindow += 1;
  // Never console.* from here: a failed forward would forward itself.
  void invoke("log_from_ui", { level, message: `[${windowName()}] ${message}` }).catch(
    () => undefined,
  );
}

/** Note something in the app's log (no console output). */
export function logToFile(level: "error" | "warn" | "info", message: string) {
  if (isInTauri()) send(level, message);
}

/** Install once, at startup. A no-op outside the desktop app. */
export function installUiLogForwarding() {
  if (installed || !isInTauri()) return;
  installed = true;

  for (const level of ["error", "warn"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      original(...args);
      send(level, formatLogArgs(args));
    };
  }
  window.addEventListener("error", (e) => {
    send("error", `uncaught: ${formatLogArgs([e.error ?? e.message])}`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    send("error", `unhandled rejection: ${formatLogArgs([e.reason])}`);
  });
}
