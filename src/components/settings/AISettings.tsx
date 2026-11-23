/**
 * The Groq key field for spoken requests. Where speech is recognised is no
 * longer a choice (2026-11-23): Apple silicon runs the on-device engine, and
 * other computers present by hand.
 */

import { useEffect, useState } from "react";

import {
  refreshInstructionModels,
  setInstructionModelApiKey,
  useInstructionModels,
} from "@/lib/instructionModels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { openLink } from "@/lib/tauri";
import { useToast } from "@/lib/toast";

/** The Groq key for spoken requests (`api_keys.py`). Shows "saved" once
 * there is one.
 *
 * @deprecated Groq is deprecated (2026-11-23): its free tier can't carry a
 * service and paid tiers aren't available. Hosted speech is gone; spoken
 * requests still use this key until they have another home. */
export function GroqKeyField() {
  const toast = useToast();
  const models = useInstructionModels();
  const [keyDraft, setKeyDraft] = useState("");

  useEffect(() => {
    if (!models.seen) refreshInstructionModels().catch(() => undefined);
  }, [models.seen]);

  const groqHasKey = models.models.some((m) => m.provider === "groq" && m.has_api_key);

  async function saveKey() {
    try {
      await setInstructionModelApiKey("groq", keyDraft.trim());
      setKeyDraft("");
      await refreshInstructionModels();
      toast.success("Groq key saved.");
    } catch (err) {
      toast.error(`Couldn't save the key: ${err instanceof Error ? err.message : err}`);
    }
  }

  if (groqHasKey) return <div className="text-xs text-muted-foreground">Groq key saved.</div>;
  return (
    <div className="space-y-1 pt-1">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void saveKey();
        }}
      >
        <Input
          type="password"
          value={keyDraft}
          onChange={(e) => setKeyDraft(e.target.value)}
          placeholder="Groq API key"
          aria-label="Groq API key"
          className="h-9 max-w-xs"
        />
        <Button type="submit" size="sm" variant="outline" disabled={!keyDraft.trim()}>
          Save key
        </Button>
      </form>
      <button
        type="button"
        className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        onClick={() => void openLink("https://console.groq.com/keys").catch(() => undefined)}
      >
        Get a free key from Groq
      </button>
    </div>
  );
}
