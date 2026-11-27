import { beforeEach, describe, expect, it, vi } from "vitest";

const settings = vi.fn();
const status = vi.fn();
const asr = vi.fn();

vi.mock("@/lib/tauri", () => ({
  getSettings: () => settings(),
  hostedAiStatus: () => status(),
  updateSettings: vi.fn(),
  openLink: vi.fn(),
}));
vi.mock("@/lib/audioInputPrefs", () => ({
  loadAudioInputPrefs: () => ({}),
  resolveLiveAsr: () => asr(),
}));

import { shouldAskOnlineSpeech } from "@/components/onboarding/OnlineSpeechPrompt";

describe("the Listen online question", () => {
  beforeEach(() => {
    settings.mockResolvedValue({ online_speech: null });
    status.mockResolvedValue({ org_name: "Grace", own_groq_key: false, speech: true });
    asr.mockResolvedValue({ backend: "whisper", model: "small.en" });
  });

  it("is asked once, where the plan has speech and Whisper would listen", async () => {
    expect(await shouldAskOnlineSpeech()).toEqual({ ask: true, done: true });
  });

  it("isn't asked again once answered", async () => {
    settings.mockResolvedValue({ online_speech: false });
    expect(await shouldAskOnlineSpeech()).toEqual({ ask: false, done: true });
  });

  it("waits for the plan's speech to arrive", async () => {
    status.mockResolvedValue({ org_name: null, own_groq_key: false, speech: false });
    expect(await shouldAskOnlineSpeech()).toEqual({ ask: false, done: false });
  });

  it("is never asked on Apple silicon", async () => {
    asr.mockResolvedValue({ backend: "mlx", model: "small.en" });
    expect(await shouldAskOnlineSpeech()).toEqual({ ask: false, done: true });
  });
});
