/**
 * Persistence for audio-input preferences (selected device, input
 * source type). Lives in localStorage so it survives reloads without a
 * round-trip to the Rust settings store — preferences here are tied to
 * the local machine's audio hardware and shouldn't sync via the
 * library export.
 */

import { requestSidecar } from "@/lib/sidecarClient";

const STORAGE_KEY = "auto-presenter.audio-input-prefs";

export type AudioInputSource = "vocal_aux" | "microphone" | "unknown";

export interface AudioInputPrefs {
  /** Selected sounddevice index; null = use system default. */
  device_index: number | null;
  /**
   * Selected device name (PortAudio's reported name string). Saved
   * alongside the index because indices reshuffle across reboots / USB
   * hot-plug — pinning the name lets the sidecar substring-match the
   * right device even when the index has shifted.
   */
  device_name: string | null;
  source: AudioInputSource;
  /** True once the operator has completed the setup wizard at least once. */
  setup_completed: boolean;
  /**
   * faster-whisper / MLX model size when `asr_backend` is "whisper" or "mlx"
   * (e.g. "tiny.en"), or the Groq model name when `asr_backend="groq"` (e.g.
   * "whisper-large-v3-turbo"). Used for live transcription (present mode +
   * the debug panel's default).
   */
  asr_model: string;
  /**
   * Which ASR backend live sessions transcribe with. "whisper" is
   * faster-whisper on CPU (or CUDA); "mlx" is Whisper on Apple silicon's
   * GPU; "groq" is hosted. Defaults to local — the shipping constraint is
   * "installable by a volunteer with no network guaranteed at service time."
   * On Apple silicon with mlx-whisper installed, `resolveLiveAsr` upgrades
   * the unset factory default to MLX.
   */
  asr_backend: "whisper" | "groq" | "mlx";
  /**
   * True once the operator (or the setup wizard) picked a backend/model.
   * When false, live sessions ask `recommend_model` instead of shipping
   * the factory `whisper` / `tiny.en` default.
   */
  asr_chosen: boolean;
}

const DEFAULT_PREFS: AudioInputPrefs = {
  device_index: null,
  device_name: null,
  source: "unknown",
  setup_completed: false,
  asr_model: "tiny.en",
  asr_backend: "whisper",
  asr_chosen: false,
};

export function loadAudioInputPrefs(): AudioInputPrefs {
  if (typeof localStorage === "undefined") return DEFAULT_PREFS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return DEFAULT_PREFS;
    return {
      device_index:
        typeof parsed.device_index === "number" ? parsed.device_index : null,
      device_name:
        typeof parsed.device_name === "string" ? parsed.device_name : null,
      source:
        parsed.source === "vocal_aux" || parsed.source === "microphone"
          ? parsed.source
          : "unknown",
      setup_completed: parsed.setup_completed === true,
      asr_model:
        typeof parsed.asr_model === "string" && parsed.asr_model
          ? parsed.asr_model
          : DEFAULT_PREFS.asr_model,
      asr_backend:
        parsed.asr_backend === "groq" || parsed.asr_backend === "mlx"
          ? parsed.asr_backend
          : "whisper",
      asr_chosen: parsed.asr_chosen === true,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

/** Fired on `window` after the prefs are saved (e.g. audio setup finished). */
export const AUDIO_PREFS_CHANGED_EVENT = "auto-presenter:audio-prefs-changed";

export function saveAudioInputPrefs(prefs: AudioInputPrefs): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Ignore quota / serialization errors — settings are non-critical.
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUDIO_PREFS_CHANGED_EVENT));
}

/**
 * Backend + model a live session should start with.
 *
 * Factory prefs are `whisper` / `tiny.en` with `asr_chosen=false`. That used
 * to be what present mode shipped, which left Apple GPU idle. Until the
 * operator (or the wizard) picks something, ask the sidecar's
 * `recommend_model` — on this host that is MLX `small.en`.
 *
 * Groq's hosted speech is deprecated (2026-11-23: its free tier can't carry a
 * service and paid tiers aren't available), so a saved Groq choice is
 * ignored and the computer's own engine is used, or no live AI.
 */
export async function resolveLiveAsr(
  prefs: AudioInputPrefs = loadAudioInputPrefs(),
): Promise<{ backend: AudioInputPrefs["asr_backend"]; model: string }> {
  if (prefs.asr_chosen && prefs.asr_backend !== "groq") {
    return onCpuPath({ backend: prefs.asr_backend, model: prefs.asr_model });
  }
  try {
    const rec = await requestSidecar({ kind: "recommend_model" });
    const backend = rec.backend === "mlx" ? "mlx" : "whisper";
    return onCpuPath({ backend, model: rec.model_size });
  } catch {
    const backend = prefs.asr_backend === "groq" ? "whisper" : prefs.asr_backend;
    return onCpuPath({ backend, model: prefs.asr_model });
  }
}

/** The model live following's CPU path was measured with, and the only one
 * the machine check judges (sidecar `asr/machine_check.py`). */
export const CPU_PATH_MODEL = "small.en";

/** On the local CPU engine the model is always `CPU_PATH_MODEL`: older
 * versions saved tiny.en/base.en from a core-count guess, which measured far
 * below the accuracy bar (2026-10-15), and a computer too slow for small.en
 * now gets no live AI rather than a worse one. */
function onCpuPath(asr: { backend: AudioInputPrefs["asr_backend"]; model: string }) {
  return asr.backend === "whisper" ? { backend: asr.backend, model: CPU_PATH_MODEL } : asr;
}
