/**
 * "Something not working?" — save a diagnostics file to send to the
 * developers, or open the folder the log lives in.
 */

import { useState } from "react";
import { FileDown, FolderOpen, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openLogFolder, saveDiagnostics } from "@/lib/diagnosticsReport";
import { InfoTip } from "@/components/ui/InfoTip";

type Result = { kind: "saved"; path: string } | { kind: "error"; message: string } | null;

export function DiagnosticsExport() {
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<Result>(null);

  async function save() {
    setSaving(true);
    setResult(null);
    try {
      setResult({ kind: "saved", path: await saveDiagnostics() });
    } catch (err) {
      setResult({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="diagnostics-export">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        Something not working?
        <InfoTip>
          The file has this computer&rsquo;s details, the AI&rsquo;s status and the app&rsquo;s
          recent log, which can include this computer&rsquo;s name, its network addresses, song
          lyrics and spoken commands it heard. No audio.
        </InfoTip>
      </div>
      <p className="text-xs text-muted-foreground">Save a file to send to the developers.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={save} disabled={saving}>
          {saving ? (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
          ) : (
            <FileDown className="mr-1.5 size-3.5" />
          )}
          {saving ? "Saving…" : "Save diagnostics"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            void openLogFolder().catch((err: unknown) =>
              setResult({ kind: "error", message: err instanceof Error ? err.message : String(err) }),
            )
          }
        >
          <FolderOpen className="mr-1.5 size-3.5" />
          Open log folder
        </Button>
      </div>
      {result?.kind === "saved" && (
        <p className="text-xs text-emerald-400" role="status">
          Saved to {result.path}. Attach it to an email to the developers.
        </p>
      )}
      {result?.kind === "error" && (
        <p className="text-xs text-red-400" role="alert">
          Couldn&rsquo;t save it: {result.message}
        </p>
      )}
    </div>
  );
}
