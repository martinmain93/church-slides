/**
 * Live ASR stream from the sidecar (Phase 4) — start/stop a transcription
 * session and watch words arrive in real time, on whichever backend is
 * selected: the mock demo, local Whisper, or Groq's hosted Whisper.
 */

import { useEffect, useState } from "react";
import { Play, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ModelDownloadProgress } from "@/components/ui/ModelDownloadProgress";
import { cn } from "@/lib/utils";
import {
  refreshInstructionModels,
  setInstructionModelApiKey,
  useInstructionModels,
} from "@/lib/instructionModels";
import { useTranscriptStream, type AudioSessionState } from "@/lib/transcriptStream";
import { loadAudioInputPrefs, saveAudioInputPrefs } from "@/lib/audioInputPrefs";
import type { Word } from "@/types/generated";

const BACKEND_STORAGE_KEY = "church_slides_debug_backend_v1";

const WHISPER_MODEL_OPTIONS = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"];
//: Groq's hosted Whisper models, per its API docs. `-turbo` is the fast,
//: cheap default; the two others trade speed for accuracy or drop to
//: English-only for a bit more speed.
const GROQ_MODEL_OPTIONS = [
  "whisper-large-v3-turbo",
  "whisper-large-v3",
  "distil-whisper-large-v3-en",
];

type AsrBackend = "mock" | "whisper" | "groq" | "mlx";

// Words are laid out as wrapping flex items rather than inline spans separated
// by a margin. Adjacent inline boxes with no whitespace between them give the
// browser no break opportunity, so the whole transcript became one unbreakable
// run that ran off the right edge — leaving only the first few words visible,
// which is exactly when a live transcript stops being useful.
export const WORD_LIST = "flex flex-wrap gap-x-1";

/**
 * How many words of transcript to show at once.
 *
 * The panel is for judging whether the words are any good, and that is a
 * glance at the last line or two. More than that and the useful part — what
 * just arrived — is buried in scrollback. At the measured rate of about one
 * word a second this is roughly the last fifteen seconds.
 */
const VISIBLE_WORDS = 15;

export function TranscriptTab() {
  const stream = useTranscriptStream();
  const models = useInstructionModels();
  // Persist the backend choice across panel open/close (it's local state that
  // would otherwise reset to "mock" every time the panel unmounts).
  const [backend, setBackend] = useState<AsrBackend>(() => {
    try {
      const saved = localStorage.getItem(BACKEND_STORAGE_KEY);
      return saved === "whisper" || saved === "groq" || saved === "mlx"
        ? saved
        : "mock";
    } catch {
      return "mock";
    }
  });
  // The model is shared with present mode via the audio-input prefs, so the
  // size picked here is also what live services transcribe with.
  const [modelSize, setModelSize] = useState(() => loadAudioInputPrefs().asr_model);
  const [groqKeyDraft, setGroqKeyDraft] = useState("");
  const isRunning = stream.session === "running" || stream.session === "starting";

  // `models` is a shared, module-level store (see `instructionModels.ts`) —
  // if the operator opens this tab before ever visiting Spoken
  // Instructions, it's still empty. Only worth fetching once Groq is
  // actually selected; picking "mock" or local Whisper shouldn't cost a
  // sidecar round trip.
  useEffect(() => {
    if (backend === "groq" && !models.seen) {
      refreshInstructionModels().catch(console.error);
    }
  }, [backend, models.seen]);

  // Reuses `decision.instructions`'s Groq key — same account, same key,
  // whether it's driving chat completions or a Whisper call. `models` is
  // already fetched for the Spoken Instructions tab's picker, so this is a
  // lookup, not a second round trip.
  const groqHasKey = models.models.some((m) => m.provider === "groq" && m.has_api_key);

  function chooseBackend(next: AsrBackend) {
    setBackend(next);
    try {
      localStorage.setItem(BACKEND_STORAGE_KEY, next);
    } catch {
      // non-critical
    }
    // Local Whisper sizes and MLX repo aliases share a namespace ("small.en"
    // resolves either way); Groq's hosted model names do not.
    const fallback =
      next === "groq" ? "whisper-large-v3-turbo" : next === "mlx" ? "small.en" : "tiny.en";
    setModelSize(fallback);
    if (next !== "mock") {
      saveAudioInputPrefs({
        ...loadAudioInputPrefs(),
        asr_backend: next,
        asr_model: fallback,
        asr_chosen: true,
      });
    }
  }

  function chooseModel(next: string) {
    setModelSize(next);
    saveAudioInputPrefs({
      ...loadAudioInputPrefs(),
      asr_model: next,
      asr_chosen: true,
    });
  }

  async function toggle() {
    try {
      if (isRunning) {
        await stream.stop();
      } else {
        await stream.start({ backend, modelSize });
      }
    } catch {
      // errors surface via stream.error
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <SessionPill state={stream.session} connected={stream.connected} />
          <select
            value={backend}
            onChange={(e) => chooseBackend(e.target.value as AsrBackend)}
            disabled={isRunning || !stream.connected}
            aria-label="ASR backend"
            className="h-6 rounded border border-white/10 bg-zinc-900 px-2 text-[11px] disabled:opacity-50"
          >
            <option value="mock">Mock (demo)</option>
            <option value="whisper">Whisper (local)</option>
            <option value="mlx">Whisper (MLX, Apple GPU)</option>
            <option value="groq">Whisper (Groq, hosted)</option>
          </select>
          {(backend === "whisper" || backend === "mlx") && (
            <select
              value={modelSize}
              onChange={(e) => chooseModel(e.target.value)}
              disabled={isRunning || !stream.connected}
              aria-label="Whisper model size"
              className="h-6 rounded border border-white/10 bg-zinc-900 px-2 text-[11px] disabled:opacity-50"
            >
              {WHISPER_MODEL_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
          {backend === "groq" && (
            <>
              <select
                value={modelSize}
                onChange={(e) => chooseModel(e.target.value)}
                disabled={isRunning || !stream.connected}
                aria-label="Groq Whisper model"
                className="h-6 rounded border border-white/10 bg-zinc-900 px-2 text-[11px] disabled:opacity-50"
              >
                {GROQ_MODEL_OPTIONS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              {!groqHasKey && (
                <>
                  <input
                    type="password"
                    placeholder="Groq API key"
                    value={groqKeyDraft}
                    onChange={(e) => setGroqKeyDraft(e.target.value)}
                    className="h-6 w-32 rounded border border-white/10 bg-black/30 px-1.5 text-[11px] text-zinc-200"
                  />
                  <button
                    type="button"
                    className="h-6 shrink-0 rounded border border-white/10 bg-white/5 px-2 text-[10px] text-zinc-300 hover:bg-white/10"
                    onClick={() => {
                      setInstructionModelApiKey("groq", groqKeyDraft).catch(console.error);
                      setGroqKeyDraft("");
                    }}
                  >
                    Save key
                  </button>
                </>
              )}
            </>
          )}
        </div>
        <Button
          type="button"
          variant={isRunning ? "destructive" : "default"}
          size="sm"
          disabled={!stream.connected}
          onClick={toggle}
        >
          {isRunning ? <Square className="size-3.5" /> : <Play className="size-3.5" />}
          {isRunning ? "Stop" : "Start"}
        </Button>
      </div>
      {backend === "groq" && (
        <div className="border-b border-white/10 bg-white/5 px-4 py-1.5 text-[10px] text-zinc-400">
          Needs a network connection at service time and counts against Groq's
          free-tier request quota — measured 2026-09-03 at roughly one request
          per second of live audio, which can approach the daily limit over a
          single service. See docs/done/SPOKEN_INSTRUCTIONS.md.
        </div>
      )}
      {stream.error && (
        <div className="border-b border-rose-500/30 bg-rose-500/10 px-4 py-2 text-xs text-rose-200">
          {stream.error}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-[12px] leading-relaxed">
        {/* Whisper's first start downloads a model (tens of MB to >1 GB) before
            the session can run; without this the panel looks frozen on
            "Starting". The component self-hides when no load is in flight. */}
        <div className="mb-2 font-sans">
          <ModelDownloadProgress />
        </div>
        {stream.finalWords.length === 0 && stream.partialWords.length === 0 && (
          <p className="text-zinc-500">
            {stream.connected
              ? "Press Start to capture audio and stream transcripts."
              : "Connecting to sidecar…"}
          </p>
        )}
        <FinalWordsList words={stream.finalWords} />
        {stream.partialWords.length > 0 && (
          <div className={`mt-2 ${WORD_LIST} text-zinc-500`} data-testid="partial-words">
            {stream.partialWords.map((w, i) => (
              <span key={`${i}-${w.t_start}`}>{w.text} </span>
            ))}
            <span className="ml-1 inline-block h-3 w-1 animate-pulse bg-emerald-400" />
          </div>
        )}
      </div>
    </div>
  );
}

function FinalWordsList({ words }: { words: Word[] }) {
  if (words.length === 0) return null;
  const recent = words.slice(-VISIBLE_WORDS);
  return (
    <div data-testid="final-words" className={`${WORD_LIST} text-zinc-100`}>
      {recent.map((w, i) => (
        <span key={`${i}-${w.t_start}`}>{w.text} </span>
      ))}
    </div>
  );
}

function SessionPill({
  state,
  connected,
}: {
  state: AudioSessionState | "disconnected";
  connected: boolean;
}) {
  const labels: Record<AudioSessionState | "disconnected", string> = {
    disconnected: connected ? "Idle" : "No sidecar",
    starting: "Starting…",
    running: "Listening",
    stopping: "Stopping…",
    stopped: "Idle",
    error: "Error",
  };
  const colors: Record<AudioSessionState | "disconnected", string> = {
    disconnected: "bg-zinc-700 text-zinc-300",
    starting: "bg-amber-500/20 text-amber-300",
    running: "bg-emerald-500/20 text-emerald-300",
    stopping: "bg-amber-500/20 text-amber-300",
    stopped: "bg-zinc-700 text-zinc-300",
    error: "bg-rose-500/20 text-rose-300",
  };
  const effective = !connected ? "disconnected" : state;
  return (
    <span
      className={cn(
        "rounded-md px-2 py-0.5 text-[10px] uppercase tracking-wider",
        colors[effective],
      )}
    >
      {labels[effective]}
    </span>
  );
}
