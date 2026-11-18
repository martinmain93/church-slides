import { useState } from "react";

import { QrCode } from "@/components/band/QrCode";
import { Button } from "@/components/ui/button";
import { createBandLink } from "@/lib/account";
import { useToast } from "@/lib/toast";

/**
 * Make an organization's band or leader link and show it as a QR code to
 * scan and an address to copy. In the band screens panel (during a service)
 * and in Settings → Account → Team (ahead of one).
 */
export function BandLinkMaker({ orgId }: { orgId: string }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<{ url: string; leader: boolean } | null>(null);

  async function make(leader: boolean) {
    setBusy(true);
    try {
      const made = await createBandLink(orgId, leader ? "Worship leader" : "Band", leader);
      setLink({ url: made.url, leader });
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
      toast.error("Couldn't copy — select the link and copy it instead.");
    }
  }

  return (
    <div className="space-y-2" data-testid="band-link-maker">
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void make(false)}>
          Band link
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void make(true)}>
          Leader link
        </Button>
      </div>
      {link && (
        <div className="space-y-2">
          <div className="flex justify-center">
            <QrCode text={link.url} label={link.leader ? "Worship leader" : "Band"} />
          </div>
          <div className="flex items-center gap-2">
            <input
              readOnly
              aria-label={link.leader ? "Leader link" : "Band link"}
              value={link.url}
              onFocus={(e) => e.currentTarget.select()}
              className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-xs"
            />
            <Button type="button" size="sm" onClick={() => void copy(link.url)}>
              Copy
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {link.leader
              ? "This link also controls the slides. Give it only to whoever leads worship."
              : "Anyone with this link sees the lyrics and chords during services."}{" "}
            It&apos;s shown only now; make a new one any time.
          </p>
        </div>
      )}
    </div>
  );
}
