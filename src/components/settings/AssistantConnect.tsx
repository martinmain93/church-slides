import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  connectClaudeDesktop,
  disconnectClaudeDesktop,
  getMcpConnectionInfo,
  type McpConnectionInfo,
} from "@/lib/assistantConnect";
import { useToast } from "@/lib/toast";
import { InfoTip } from "@/components/ui/InfoTip";

const STATUS: Record<McpConnectionInfo["claude_desktop"], string> = {
  not_installed: "Claude Desktop isn't installed on this Mac.",
  not_connected: "Not connected.",
  connected: "Connected.",
  stale: "Connected to a different copy of this app — reconnect to use this one.",
};

/**
 * Settings → AI assistants: register the library's MCP server with Claude
 * Desktop in one click, or copy the details for any other MCP client.
 */
export function AssistantConnect() {
  const toast = useToast();
  const [info, setInfo] = useState<McpConnectionInfo | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [justConnected, setJustConnected] = useState(false);
  const [showOther, setShowOther] = useState(false);

  useEffect(() => {
    getMcpConnectionInfo()
      .then(setInfo)
      .catch(() => setUnavailable(true));
  }, []);

  async function run(action: () => Promise<McpConnectionInfo>, connecting: boolean) {
    setBusy(true);
    try {
      setInfo(await action());
      setJustConnected(connecting);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied.");
    } catch {
      toast.error("Couldn't copy — select the text and copy it instead.");
    }
  }

  if (unavailable) {
    return (
      <div className="text-xs text-muted-foreground">
        Available in the desktop app.
      </div>
    );
  }
  if (!info) return null;

  const state = info.claude_desktop;
  const claudeCodeCmd = `claude mcp add church-slides -- "${info.command}" --mcp`;

  return (
    <div className="space-y-4">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        Build setlists, add songs from lyrics, and tidy slide order.
        <InfoTip>
          It works on this computer&apos;s library directly, and open screens here update as it
          goes. The whole library is backed up before each batch of changes it makes.
        </InfoTip>
      </p>

      <div className="grid grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1">
        <div>
          <div className="text-sm font-medium">Claude Desktop &amp; Cowork</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {STATUS[state]}
            {justConnected && state === "connected" && (
              <> Quit and reopen Claude to finish, then ask it something like
              “make a setlist for Sunday with three upbeat songs we haven’t
              sung this month”.</>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {state === "connected" ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => run(disconnectClaudeDesktop, false)}
            >
              Disconnect
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={busy || state === "not_installed"}
              onClick={() => run(connectClaudeDesktop, true)}
            >
              {state === "stale" ? "Reconnect" : "Connect"}
            </Button>
          )}
        </div>
      </div>

      <div>
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setShowOther((v) => !v)}
        >
          {showOther ? "Hide" : "Using a different app?"}
        </button>
        {showOther && (
          <div className="mt-3 space-y-3 text-xs">
            <CopyBlock
              label="Claude Code"
              text={claudeCodeCmd}
              onCopy={copy}
            />
            <CopyBlock
              label="Cursor, Windsurf and other apps configured with JSON (mcpServers)"
              text={info.config_snippet}
              onCopy={copy}
            />
            <p className="text-muted-foreground">
              Any app that can run a local MCP server can use the command
              above.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function CopyBlock({
  label,
  text,
  onCopy,
}: {
  label: string;
  text: string;
  onCopy: (text: string) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="font-medium">{label}</span>
        <Button variant="ghost" size="sm" onClick={() => onCopy(text)}>
          Copy
        </Button>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-2 font-mono text-[11px]">
        {text}
      </pre>
    </div>
  );
}
