/**
 * The import's fallback for files it can't read: say so plainly, point at the
 * formats it does read and at an AI assistant (which can read nearly anything
 * and add songs through the library's MCP tools), and tell the developers.
 *
 * With error reports on, each unreadable file's shape (extension, size,
 * first bytes — never its contents or name) is reported once, automatically.
 * Without, one button opens "Send feedback" with the same details filled in,
 * for the person to send or not.
 */

import { useEffect, useRef, useState } from "react";

import { openFeedback } from "@/components/FeedbackDialog";
import { OPEN_SETTINGS_EVENT } from "@/components/setlists/AiReadyPill";
import { Button } from "@/components/ui/button";
import type { ImportFileResult } from "@/lib/chords/import/types";
import { getSettings, openLink, reportUnreadableImport } from "@/lib/tauri";
import { FORMATS_URL } from "@/lib/usage";

const link = "underline underline-offset-2 hover:text-foreground";

function extensionOf(name: string): string {
  const base = name.split(" › ")[0]!;
  return base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
}

export function UnreadableFiles({ failed }: { failed: ImportFileResult[] }) {
  const [reporting, setReporting] = useState<boolean | null>(null);
  const reported = useRef(false);

  useEffect(() => {
    getSettings()
      .then((s) => setReporting(s.send_error_reports))
      .catch(() => setReporting(false));
  }, []);

  useEffect(() => {
    if (!reporting || reported.current) return;
    reported.current = true;
    for (const f of failed) {
      void reportUnreadableImport(extensionOf(f.fileName), f.error ?? "unknown", f.signature ?? "").catch(() => undefined);
    }
  }, [reporting, failed]);

  const note = () =>
    openFeedback(
      `Church Slides couldn't read ${failed.length === 1 ? "this file" : "these files"}:\n` +
        failed.map((f) => `• ${f.error}${f.signature ? ` (${f.signature})` : ""}`).join("\n") +
        "\n\nWhere it came from (which app, which export option):\n",
    );

  const one = failed.length === 1;
  return (
    <div className="border-b border-border bg-muted/40 px-5 py-3 text-sm" data-testid="unreadable-files">
      <div className="font-medium">
        {one ? "We don’t recognise that file’s format." : `We don’t recognise ${failed.length} of these files.`}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        See the{" "}
        <button type="button" className={link} onClick={() => void openLink(FORMATS_URL).catch(() => undefined)}>
          formats Church Slides reads
        </button>
        , or{" "}
        <button
          type="button"
          className={link}
          onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { page: "ai" } }))}
        >
          connect an AI assistant
        </button>{" "}
        and ask it to read the file into your library.
      </p>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted-foreground">Which {one ? "file" : "files"}</summary>
        <ul className="mt-1 max-h-40 overflow-y-auto">
          {failed.map((f) => (
            <li key={f.fileName}>
              <span className="font-medium">{f.fileName}</span>: {f.error}
            </li>
          ))}
        </ul>
      </details>
      <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
        {reporting ? (
          <span>We&rsquo;ve let the developers know (the file itself wasn&rsquo;t sent).</span>
        ) : reporting === false ? (
          <Button variant="outline" size="sm" onClick={note}>
            Send note to developers
          </Button>
        ) : null}
      </div>
    </div>
  );
}
