/**
 * The catalog of models spoken instructions can run on — local (downloaded
 * via ollama) and hosted (an API key away). See
 * `decision.instruction_models` for the catalog itself and
 * `Plan/SPOKEN_INSTRUCTIONS.md` for what's actually measured vs. just
 * listed.
 *
 * Two concerns, two stores: the catalog + install/key status is
 * request/response (`refreshInstructionModels`) because it needs a fresh
 * read of ollama and the keys file, not something the sidecar broadcasts
 * unprompted. Download progress is a live broadcast, same shape as
 * `modelDownload.ts`'s Whisper equivalent.
 */

import {
  createLiveStore,
  pipeFromSidecar,
  useLiveStore,
} from "@/lib/liveStore";
import { getSharedConnection } from "@/lib/sidecarConnection";
import { requestSidecar } from "@/lib/sidecarClient";
import type {
  InstructionModelInfo,
} from "@/types/generated";

export interface InstructionModelsState {
  models: InstructionModelInfo[];
  activeModelId: string;
  ollamaAvailable: boolean;
  /** Whether a successful list has ever landed this session. */
  seen: boolean;
}

const EMPTY: InstructionModelsState = {
  models: [],
  activeModelId: "",
  ollamaAvailable: false,
  seen: false,
};

const store = createLiveStore<InstructionModelsState>(EMPTY);

export function useInstructionModels(): InstructionModelsState {
  return useLiveStore(store);
}

/** Fetch a fresh read. Call on tab mount and after anything that could
 * change install/key status (a download finishing, a key being saved). */
export async function refreshInstructionModels(): Promise<void> {
  const reply = await requestSidecar({
    kind: "list_instruction_models",
  });
  store.set({
    models: reply.models,
    activeModelId: reply.active_model_id,
    ollamaAvailable: reply.ollama_available,
    seen: true,
  });
}

export async function setInstructionModel(modelId: string): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "set_instruction_model", model_id: modelId });
  await refreshInstructionModels();
}

export async function setInstructionModelApiKey(
  provider: string,
  apiKey: string,
): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({
    kind: "set_instruction_model_api_key",
    provider,
    api_key: apiKey,
  });
  await refreshInstructionModels();
}

// --- download progress -------------------------------------------------

export interface DownloadState {
  modelId: string;
  bytesDownloaded: number;
  bytesTotal: number;
  fraction: number;
  state: "starting" | "downloading" | "completed" | "failed";
  detail: string;
}

const downloadStore = createLiveStore<DownloadState | null>(null);

pipeFromSidecar("instruction_model_download_progress", downloadStore, (msg) => {
  return {
    modelId: msg.model_id,
    bytesDownloaded: msg.bytes_downloaded,
    bytesTotal: msg.bytes_total,
    fraction: msg.fraction,
    state: msg.state,
    detail: msg.detail ?? "",
  };
});

export function useInstructionModelDownload(): DownloadState | null {
  return useLiveStore(downloadStore);
}

/**
 * Fire-and-forget: the server acks this request immediately, *before* the
 * (multi-minute) download even starts — the real progress arrives on the
 * broadcast above, ending in a `completed` or `failed` state. Callers
 * should watch `useInstructionModelDownload()` and call
 * `refreshInstructionModels()` themselves once `state === "completed"`,
 * not assume this promise resolving means the model is ready.
 */
export async function downloadInstructionModel(modelId: string): Promise<void> {
  const conn = await getSharedConnection();
  await conn.request({ kind: "download_instruction_model", model_id: modelId });
}
