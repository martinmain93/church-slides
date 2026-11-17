/**
 * The debug panel as a whole window.
 *
 * Docked, the panel covers the bottom-right of the operator screen — exactly
 * where the slide grid and setlist are — so reading it means hiding the thing
 * being debugged. Detached it sits on a second monitor beside the app.
 *
 * This renders the tabs directly rather than `DebugPanel`, which owns the
 * floating card: its own open/close state, fixed positioning and close button
 * are all the dock's concerns, and none of them mean anything in a window that
 * *is* the panel.
 *
 * Nothing is passed in. Every tab reads module-scoped live stores fed by the
 * shared sidecar socket (see `lib/liveStore`), and each window opens its own
 * connection through `getSidecarPort`, so this needs nothing from the shell it
 * was detached from.
 */

import { Component, type ReactNode } from "react";

import { DebugTabs } from "@/components/debug/DebugTabs";
import { ConfirmProvider } from "@/lib/confirm";
import { ToastProvider } from "@/lib/toast";

/**
 * Keeps one bad tab from taking the window with it.
 *
 * Docked, the panel lives inside `AppShell`, which supplies `ToastProvider`
 * and `ConfirmProvider`. Detached it does not, and `useToast` *throws* when
 * its provider is missing — so opening the window on a tab that used it
 * (`useToggleRequest`, which several go through) unmounted
 * the whole React tree. A blank near-black window, with no way back, because
 * nothing was left alive to render a way back.
 *
 * The providers below are the actual fix. This boundary is the belt: a debug
 * surface that can black itself out is worse than useless, since it fails
 * exactly when something is already wrong and being investigated.
 */
class TabBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="p-4 text-sm text-zinc-300">
        <p className="font-semibold text-red-400">This tab failed to render.</p>
        <pre className="mt-2 whitespace-pre-wrap text-xs text-zinc-400">
          {error.message}
        </pre>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-3 rounded border border-white/10 px-2 py-1 text-xs hover:text-zinc-100"
        >
          Try again
        </button>
      </div>
    );
  }
}

export function DebugWindow() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <div
          data-testid="debug-window"
          className="flex h-screen w-screen flex-col bg-zinc-950 text-zinc-100"
        >
          <TabBoundary>
            <DebugTabs />
          </TabBoundary>
        </div>
      </ConfirmProvider>
    </ToastProvider>
  );
}
