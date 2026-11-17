/**
 * "Save diagnostics": what the UI knows about live AI, handed to the shell's
 * `save_diagnostics`, which adds this computer's details and the recent log
 * and writes one text file to Downloads.
 *
 * Each probe gets its own short timeout and records its own failure: the
 * report is most needed exactly when the engine isn't answering, so one dead
 * probe must not stop the rest from being written.
 */

import { loadAudioInputPrefs, resolveLiveAsr } from "@/lib/audioInputPrefs";
import { invoke } from "@/lib/invoke";
import { requestSidecar } from "@/lib/sidecarClient";
import { getAiEngineStatus } from "@/lib/tauri";

const PROBE_MS = 3000;

async function probe<T>(fn: () => Promise<T>): Promise<T | { error: string }> {
  try {
    return await Promise.race([
      fn(),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error(`no answer within ${PROBE_MS} ms`)), PROBE_MS),
      ),
    ]);
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

export async function collectUiReport(): Promise<Record<string, unknown>> {
  const prefs = loadAudioInputPrefs();
  const [engine, asr, devices] = await Promise.all([
    probe(getAiEngineStatus),
    probe(() => resolveLiveAsr(prefs)),
    probe(() => requestSidecar({ kind: "list_audio_devices" }, { timeoutMs: PROBE_MS })),
  ]);
  const model =
    "error" in asr
      ? asr
      : await probe(() =>
          requestSidecar(
            { kind: "model_status", backend: asr.backend, model_size: asr.model },
            { timeoutMs: PROBE_MS },
          ),
        );
  return {
    ai_engine: engine,
    speech_model: { chosen: asr, status: model },
    audio_input: {
      source: prefs.source,
      device_name: prefs.device_name,
      device_index: prefs.device_index,
      setup_completed: prefs.setup_completed,
      asr_backend: prefs.asr_backend,
      asr_model: prefs.asr_model,
      asr_chosen: prefs.asr_chosen,
    },
    audio_devices:
      "error" in devices
        ? devices
        : devices.devices.map((d) => `${d.index}: ${d.name}${d.is_default ? " (default)" : ""}`),
    screen: `${window.screen.width}x${window.screen.height} @${window.devicePixelRatio}x`,
    user_agent: navigator.userAgent,
  };
}

/** Write the file and show it in Finder; resolves to its path. */
export async function saveDiagnostics(): Promise<string> {
  const uiReport = await collectUiReport();
  return invoke<string>("save_diagnostics", { uiReport });
}

export async function openLogFolder(): Promise<void> {
  await invoke<void>("open_log_folder");
}
