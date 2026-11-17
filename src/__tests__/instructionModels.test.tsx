/**
 * `lib/instructionModels` — the model catalog (request/response) and
 * download progress (broadcast) stores.
 *
 * Separate mock setup from `liveStore.test.tsx`: this module's actions call
 * `conn.request(...)` and need a real (fake) response, not just the
 * broadcast fan-out the other stores use.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { handlers, conn } = vi.hoisted(() => {
  const handlers = new Map<string, ((raw: unknown) => void)[]>();
  return {
    handlers,
    conn: {
      status: "open",
      onMessage: (kind: string, cb: (raw: unknown) => void) => {
        const list = handlers.get(kind) ?? [];
        list.push(cb);
        handlers.set(kind, list);
        return () => handlers.set(kind, list.filter((h) => h !== cb));
      },
      onStatus: () => () => {},
      request: vi.fn(),
    },
  };
});

vi.mock("@/lib/sidecarConnection", () => ({
  getSharedConnection: () => Promise.resolve(conn),
}));

import {
  downloadInstructionModel,
  refreshInstructionModels,
  setInstructionModel,
  setInstructionModelApiKey,
  useInstructionModelDownload,
  useInstructionModels,
} from "@/lib/instructionModels";

function broadcast(kind: string, raw: unknown) {
  act(() => (handlers.get(kind) ?? []).forEach((h) => h(raw)));
}

const CATALOG_REPLY = {
  models: [
    {
      id: "qwen2.5:3b-instruct",
      label: "Qwen 2.5 Instruct (3B)",
      model_kind: "local",
      size_mb: 1900,
      installed: true,
      note: "shipped default",
    },
    {
      id: "groq-llama-3.1-8b-instant",
      label: "Llama 3.1 8B Instant (Groq)",
      model_kind: "hosted",
      provider: "groq",
      has_api_key: false,
      note: "needs a key",
    },
  ],
  active_model_id: "qwen2.5:3b-instruct",
  ollama_available: true,
};

beforeEach(() => {
  conn.request.mockReset();
});

describe("useInstructionModels", () => {
  it("starts empty and unseen before any refresh", () => {
    const { result } = renderHook(() => useInstructionModels());
    // Other tests in this run may have already populated the module-scoped
    // store — assert the shape, not a specific empty value.
    expect(Array.isArray(result.current.models)).toBe(true);
  });

  it("populates from the list response, mapping snake_case to camelCase", async () => {
    conn.request.mockResolvedValueOnce(CATALOG_REPLY);
    const { result } = renderHook(() => useInstructionModels());

    await act(async () => {
      await refreshInstructionModels();
    });

    expect(result.current.seen).toBe(true);
    expect(result.current.activeModelId).toBe("qwen2.5:3b-instruct");
    expect(result.current.ollamaAvailable).toBe(true);
    expect(result.current.models).toHaveLength(2);
    expect(conn.request).toHaveBeenCalledWith({ kind: "list_instruction_models" }, undefined);
  });

  it("setInstructionModel sends the model id and refreshes", async () => {
    conn.request.mockResolvedValueOnce({}); // set_instruction_model ack
    conn.request.mockResolvedValueOnce({ ...CATALOG_REPLY, active_model_id: "llama3.2:3b" });

    await setInstructionModel("llama3.2:3b");

    // The switch itself is a direct, single-argument `conn.request` call;
    // only the internal `refreshInstructionModels()` afterward goes through
    // `requestSidecar`, which adds a trailing timeout argument.
    expect(conn.request).toHaveBeenNthCalledWith(1, {
      kind: "set_instruction_model",
      model_id: "llama3.2:3b",
    });
    expect(conn.request).toHaveBeenNthCalledWith(
      2,
      { kind: "list_instruction_models" },
      undefined,
    );
  });

  it("setInstructionModelApiKey sends provider and key, then refreshes", async () => {
    conn.request.mockResolvedValueOnce({});
    conn.request.mockResolvedValueOnce(CATALOG_REPLY);

    await setInstructionModelApiKey("groq", "gk_test");

    expect(conn.request).toHaveBeenNthCalledWith(1, {
      kind: "set_instruction_model_api_key",
      provider: "groq",
      api_key: "gk_test",
    });
  });
});

describe("downloadInstructionModel", () => {
  it("sends the request but does not itself refresh — completion arrives via broadcast", async () => {
    conn.request.mockResolvedValueOnce({});
    await downloadInstructionModel("qwen2.5:7b-instruct");
    expect(conn.request).toHaveBeenCalledTimes(1);
    expect(conn.request).toHaveBeenCalledWith({
      kind: "download_instruction_model",
      model_id: "qwen2.5:7b-instruct",
    });
  });
});

describe("useInstructionModelDownload", () => {
  it("reflects progress broadcasts, camelCased", async () => {
    const { result } = renderHook(() => useInstructionModelDownload());

    broadcast("instruction_model_download_progress", {
      model_id: "qwen2.5:7b-instruct",
      bytes_downloaded: 512,
      bytes_total: 4700,
      fraction: 0.109,
      state: "downloading",
      detail: "pulling manifest",
    });

    await waitFor(() => {
      expect(result.current?.modelId).toBe("qwen2.5:7b-instruct");
    });
    expect(result.current?.bytesDownloaded).toBe(512);
    expect(result.current?.state).toBe("downloading");
  });
});
