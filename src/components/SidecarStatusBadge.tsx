import { useEffect, useState } from "react";
import { Circle } from "lucide-react";

import { type ConnectionStatus } from "@/lib/ws";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { getAiEngineStatus } from "@/lib/tauri";
import { cn } from "@/lib/utils";

export function SidecarStatusBadge() {
  const [status, setStatus] = useState<ConnectionStatus | "init" | "missing" | "unavailable">(
    "init",
  );

  useEffect(() => {
    let cancelled = false;
    let unsubStatus: (() => void) | null = null;

    (async () => {
      try {
        const conn = await getSharedConnection();
        if (cancelled) return;
        setStatus(conn.status);
        unsubStatus = conn.onStatus((s) => {
          if (!cancelled) setStatus(s);
        });
      } catch {
        // No engine on this machine or in this build is a normal way to run
        // the app (presenting by hand), not a fault — say so in grey, not red.
        const engine = await getAiEngineStatus().catch(() => null);
        if (cancelled) return;
        if (engine?.state === "failed") {
          setStatus("unavailable");
        } else {
          setStatus("missing");
        }
      }
    })();

    return () => {
      cancelled = true;
      unsubStatus?.();
      // Shared connection — only unsubscribe, never close it.
    };
  }, []);

  // When the engine is ready (or absent by design on a manual-presenting
  // build), stay out of the way — AI readiness is shown in the setlist and
  // operator bars where it matters. Only surface a dot when connecting or
  // if an active connection dropped.
  if (
    status === "open" ||
    status === "init" ||
    status === "missing" ||
    status === "unavailable"
  ) {
    return null;
  }

  const color =
    status === "connecting"
      ? "fill-amber-500 text-amber-500 animate-pulse"
      : "fill-rose-500 text-rose-500";

  const label = status === "connecting" ? "Connecting to AI engine…" : "AI engine disconnected";

  return (
    <span title={label} className="inline-flex items-center" data-testid="sidecar-badge">
      <Circle className={cn("size-2.5", color)} />
    </span>
  );
}
