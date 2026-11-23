import { beforeEach, describe, expect, it, vi } from "vitest";

const requestSidecar = vi.fn();

vi.mock("@/lib/sidecarClient", () => ({
  requestSidecar: (...args: unknown[]) => requestSidecar(...args),
}));

import {
  loadAudioInputPrefs,
  CPU_PATH_MODEL,
  resolveLiveAsr,
  saveAudioInputPrefs,
  type AudioInputPrefs,
} from "@/lib/audioInputPrefs";

const factory: AudioInputPrefs = {
  device_index: null,
  device_name: null,
  source: "unknown",
  setup_completed: false,
  asr_model: "tiny.en",
  asr_backend: "whisper",
  asr_chosen: false,
};

describe("resolveLiveAsr", () => {
  beforeEach(() => {
    localStorage.clear();
    requestSidecar.mockReset();
  });

  it("asks recommend_model when the operator has not picked a backend", async () => {
    requestSidecar.mockResolvedValue({
      kind: "recommend_model_response",
      model_size: "small.en",
      backend: "mlx",
    });
    const asr = await resolveLiveAsr(factory);
    expect(asr).toEqual({ backend: "mlx", model: "small.en" });
    expect(requestSidecar).toHaveBeenCalledWith({ kind: "recommend_model" });
  });

  it("keeps an explicit wizard/debug choice on Apple silicon", async () => {
    const asr = await resolveLiveAsr({
      ...factory,
      asr_backend: "mlx",
      asr_model: "tiny.en",
      asr_chosen: true,
    });
    expect(asr).toEqual({ backend: "mlx", model: "tiny.en" });
    expect(requestSidecar).not.toHaveBeenCalled();
  });

  it("runs the CPU engine on small.en, whatever an older version saved", async () => {
    // tiny.en/base.en came from an old core-count guess and measured far
    // below the accuracy bar; small.en is what the machine check judges
    const asr = await resolveLiveAsr({
      ...factory,
      asr_backend: "whisper",
      asr_model: "tiny.en",
      asr_chosen: true,
    });
    expect(asr).toEqual({ backend: "whisper", model: CPU_PATH_MODEL });
  });

  it("ignores a saved Groq choice: hosted speech is deprecated", async () => {
    requestSidecar.mockResolvedValue({ backend: "mlx", model_size: "small.en" });
    const asr = await resolveLiveAsr({
      ...factory,
      asr_backend: "groq",
      asr_model: "whisper-large-v3-turbo",
      asr_chosen: true,
    });
    expect(asr).toEqual({ backend: "mlx", model: "small.en" });
  });

  it("falls back to stored prefs when the sidecar is down", async () => {
    requestSidecar.mockRejectedValue(new Error("sidecar unavailable"));
    const asr = await resolveLiveAsr(factory);
    expect(asr).toEqual({ backend: "whisper", model: CPU_PATH_MODEL });
  });

  it("round-trips asr_chosen through localStorage", () => {
    saveAudioInputPrefs({ ...factory, asr_chosen: true, asr_backend: "mlx" });
    expect(loadAudioInputPrefs().asr_chosen).toBe(true);
    expect(loadAudioInputPrefs().asr_backend).toBe("mlx");
  });
});
