/**
 * App-wide debug panel.
 *
 * Triggered globally by `Cmd/Ctrl+D`. Tabs:
 *   - Transcript: live ASR stream from the sidecar (Phase 4)
 *   - Belief state: top-3 tracker hypotheses (Phase 6)
 *   - Instructions: spoken-instruction model picker + utterance history
 *   - Tunables: live-editable matcher params (Phase 6)
 *   - Perf: sidecar CPU / memory and where its time goes
 *
 * Input device + level meter live in the separate `AudioSetupWizard`
 * (Settings), not here — an earlier "Audio" tab here was never
 * implemented and always fell through to a placeholder.
 *
 * Each tab beyond the first three built (Belief state, Tunables, Perf) gets
 * its own file here, same convention — this file only owns the shell (open
 * state, tab switching, persistence).
 */

import { useEffect, useState } from "react";
import { ExternalLink, X } from "lucide-react";

import { DebugTabs } from "@/components/debug/DebugTabs";
import { matchKey } from "@/lib/presentation/keymap";
import {
  closeDebugWindow,
  isDebugWindowOpen,
  openDebugWindow,
} from "@/lib/presentation/projection";

export function DebugPanel() {
  const [open, setOpen] = useState(false);
  const [detached, setDetached] = useState(false);

  // The window may already be open from a previous session, or have been
  // closed by its own title bar — neither goes through this component, so the
  // dock asks rather than assuming.
  useEffect(() => {
    let alive = true;
    void isDebugWindowOpen().then((is) => {
      if (alive) setDetached(is);
    });
    return () => {
      alive = false;
    };
  }, [open]);

  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const match = matchKey(
        [
          {
            keys: ["d", "D"],
            action: "toggleDebug",
            meta: true,
            description: "Toggle debug panel",
          },
          {
            keys: ["d", "D"],
            action: "toggleDebug",
            ctrl: true,
            description: "Toggle debug panel",
          },
        ],
        ev,
      );
      if (!match) return;
      ev.preventDefault();
      setOpen((o) => !o);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function detach() {
    await openDebugWindow();
    setDetached(true);
    // Close the dock: two live copies of the same panel is twice the render
    // cost for one reader, and this pipeline has been starved by a render
    // loop before.
    setOpen(false);
  }

  if (!open) return null;

  // Detached, the dock is an empty frame whose content lives elsewhere. Say
  // so and offer the way back, rather than rendering a second copy.
  if (detached) {
    return (
      <aside
        data-testid="debug-panel"
        className="fixed bottom-4 right-4 z-50 flex w-[320px] flex-col rounded-lg border border-white/10 bg-zinc-950/95 p-3 text-zinc-100 shadow-2xl backdrop-blur"
      >
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Debug
          </span>
          <button
            type="button"
            aria-label="Close debug panel"
            onClick={() => setOpen(false)}
            className="text-zinc-400 hover:text-zinc-100"
          >
            <X className="size-4" />
          </button>
        </div>
        <p className="mt-2 text-xs text-zinc-400">Open in its own window.</p>
        <button
          type="button"
          onClick={async () => {
            await closeDebugWindow();
            setDetached(false);
          }}
          className="mt-2 self-start rounded border border-white/10 px-2 py-1 text-[11px] text-zinc-300 hover:text-zinc-100"
        >
          Dock it back here
        </button>
      </aside>
    );
  }

  return (
    <aside
      data-testid="debug-panel"
      className="fixed bottom-4 right-4 z-50 flex h-[480px] w-[630px] flex-col rounded-lg border border-white/10 bg-zinc-950/95 text-zinc-100 shadow-2xl backdrop-blur"
    >
      <header className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
          Debug
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Open debug panel in its own window"
            title="Open in its own window"
            onClick={() => void detach()}
            className="text-zinc-400 hover:text-zinc-100"
          >
            <ExternalLink className="size-4" />
          </button>
          <button
            type="button"
            aria-label="Close debug panel"
            onClick={() => setOpen(false)}
            className="text-zinc-400 hover:text-zinc-100"
          >
            <X className="size-4" />
          </button>
        </div>
      </header>
      <DebugTabs />
    </aside>
  );
}
