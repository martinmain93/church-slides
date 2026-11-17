/**
 * The AI rows of Settings: where speech is recognised. Whether the AI is on is not a setting — it's switched live from the
 * AI button in the service (see `aiPrefs.ts`).
 */

import { useEffect, useState } from "react";

import {
  loadAudioInputPrefs,
  saveAudioInputPrefs,
} from "@/lib/audioInputPrefs";
import {
  refreshInstructionModels,
  setInstructionModelApiKey,
  useInstructionModels,
} from "@/lib/instructionModels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { openLink } from "@/lib/tauri";
import { useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** Groq's hosted Whisper — what the "Groq" choice transcribes with. */
const GROQ_ASR_MODEL = "whisper-large-v3-turbo";

/**
 * On this computer (the default, offline) or Groq's hosted Whisper, which
 * needs a key and the internet but takes the load off a slow laptop.
 */
export function SpeechSettings() {
  const [where, setWhere] = useState<"local" | "groq">(() =>
    loadAudioInputPrefs().asr_backend === "groq" ? "groq" : "local",
  );

  function choose(next: "local" | "groq") {
    setWhere(next);
    const prefs = loadAudioInputPrefs();
    saveAudioInputPrefs(
      next === "groq"
        ? { ...prefs, asr_backend: "groq", asr_model: GROQ_ASR_MODEL, asr_chosen: true }
        : // Back to the engine's own pick for this computer's hardware.
          { ...prefs, asr_backend: "whisper", asr_model: "small.en", asr_chosen: false },
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="text-sm font-medium">Where speech is recognised</div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Where speech is recognised">
        <Choice
          selected={where === "local"}
          onClick={() => choose("local")}
          title="On this computer"
          description="Recommended. Works with no internet. The model downloads once."
        />
        <Choice
          selected={where === "groq"}
          onClick={() => choose("groq")}
          title="Groq (online)"
          description="For a slower computer. Needs the internet during the service and a free Groq API key."
        />
      </div>
      {where === "groq" && <GroqKeyField />}
    </div>
  );
}

/** The Groq key: one account key, used by online speech and by spoken
 * requests alike (`api_keys.py`), so it's entered once wherever it's asked
 * for. Shows "saved" once there is one. */
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

function Choice({
  selected,
  onClick,
  title,
  description,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  description: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-1 rounded-lg border bg-card px-3 py-2 text-left text-xs transition-colors",
        selected
          ? "border-emerald-500 bg-emerald-500/10"
          : "border-border hover:border-foreground/40 hover:bg-accent/40",
      )}
    >
      <div className="text-sm font-medium">{title}</div>
      <div className="text-muted-foreground">{description}</div>
    </button>
  );
}
