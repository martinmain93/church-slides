/**
 * Outside AI assistants (Claude Desktop, Cowork, Cursor, …) working on the
 * library through the app's MCP server (`src-tauri/src/mcp`).
 *
 * Two halves: registering the server with a client (Settings), and hearing
 * about the edits an assistant makes so open views refresh.
 */

import { useEffect, useRef } from "react";

import { invoke, isInTauri } from "@/lib/invoke";

export type ClaudeDesktopState =
  | "not_installed"
  | "not_connected"
  | "connected"
  | "stale";

export interface McpConnectionInfo {
  command: string;
  args: string[];
  config_snippet: string;
  claude_desktop: ClaudeDesktopState;
  claude_desktop_config_path: string | null;
}

export function getMcpConnectionInfo(): Promise<McpConnectionInfo> {
  return invoke<McpConnectionInfo>("mcp_connection_info");
}

export function connectClaudeDesktop(): Promise<McpConnectionInfo> {
  return invoke<McpConnectionInfo>("mcp_connect_claude_desktop");
}

export function disconnectClaudeDesktop(): Promise<McpConnectionInfo> {
  return invoke<McpConnectionInfo>("mcp_disconnect_claude_desktop");
}

/** Emitted by `library_watch.rs` when another process changed the library. */
export const LIBRARY_CHANGED_EVENT = "library-changed";

/**
 * Call `onChange` whenever something outside this app edits the library.
 * The app's own edits don't fire it — the views that made them already know.
 */
export function useLibraryChanged(onChange: () => void): void {
  const latest = useRef(onChange);
  latest.current = onChange;

  useEffect(() => {
    if (!isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const off = await listen(LIBRARY_CHANGED_EVENT, () => latest.current());
      if (cancelled) off();
      else unlisten = off;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
}
