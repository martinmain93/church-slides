/**
 * Settings → "Open-source licences": the THIRD-PARTY-NOTICES.txt bundled with
 * the app (scripts/third_party_notices.py), shown in a dialog. MIT, BSD and
 * Apache-2.0 components ask for their licence to travel with the app; this
 * is where a user finds it.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { readThirdPartyNotices } from "@/lib/tauri";

export function OpenSourceLicencesLink({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={className ?? "underline underline-offset-2 hover:text-foreground"}
        onClick={() => setOpen(true)}
      >
        Open-source licences
      </button>
      {open && <OpenSourceLicencesDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function OpenSourceLicencesDialog({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    readThirdPartyNotices()
      .then(setText)
      .catch((err: unknown) => setError(String(err)));
  }, []);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      // Don't let the presentation keymap also see it.
      ev.preventDefault();
      ev.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="open-source-licences-title"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div className="flex h-full max-h-[48rem] w-full max-w-3xl flex-col rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <h2 id="open-source-licences-title" className="text-base font-semibold">
          Open-source licences
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Church Slides includes open-source components, which remain under their own licences.
        </p>
        <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border bg-background p-3">
          {error ? (
            <p className="text-sm text-destructive">Couldn&rsquo;t open the licences: {error}</p>
          ) : text === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-snug">
              {text}
            </pre>
          )}
        </div>
        <div className="mt-4 flex justify-end">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
