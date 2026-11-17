import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const voice = vi.hoisted(() => ({
  requestVoiceChart: vi.fn(),
  startVoiceRecording: vi.fn(),
  warmVoiceInput: vi.fn(),
  releaseVoiceInput: vi.fn(),
}));
vi.mock("@/lib/chords/voiceChart", async (orig) => ({
  ...(await orig<typeof import("@/lib/chords/voiceChart")>()),
  ...voice,
}));

const models = vi.hoisted(() => ({
  state: { models: [] as { provider: string; has_api_key: boolean }[], activeModelId: "", ollamaAvailable: false, seen: true },
}));
vi.mock("@/lib/instructionModels", () => ({
  useInstructionModels: () => models.state,
  refreshInstructionModels: vi.fn(async () => undefined),
}));

import { SongEditor } from "@/components/library/SongEditor";
import { ConfirmProvider } from "@/lib/confirm";
import { CHANGED_WHILE_CHARTING, useVoiceChart } from "@/lib/chords/useVoiceChart";
import { ToastProvider } from "@/lib/toast";

type OnUtterance = (blob: Blob, final: boolean) => Promise<"applied" | "incomplete" | "error">;

beforeEach(() => {
  Object.values(voice).forEach((fn) => fn.mockReset());
  models.state = { ...models.state, models: [] };
});
afterEach(cleanup);

/** Start a take and hand back the recorder's utterance callback. */
async function startTake(hook: { current: ReturnType<typeof useVoiceChart> }): Promise<OnUtterance> {
  let onUtterance: OnUtterance | null = null;
  voice.startVoiceRecording.mockImplementation(async (handlers: { onUtterance: OnUtterance }) => {
    onUtterance = handlers.onUtterance;
    return { stop: vi.fn(async () => undefined) };
  });
  await act(async () => {
    await hook.current.toggle();
  });
  return onUtterance!;
}

const take = { transcript: "the verses are 1 4 5 1", body: "charted", chordText: "G\ncharted", chart: null, applied: "x" };

describe("useVoiceChart", () => {
  it("applies a take when the song didn't change while it was charted", async () => {
    const song = { body: "before", chart: null };
    const apply = vi.fn();
    const onError = vi.fn();
    voice.requestVoiceChart.mockResolvedValue(take);
    const { result } = renderHook(() =>
      useVoiceChart({ enabled: true, slideEdits: false, read: () => ({ ...song }), apply, onError }),
    );
    const onUtterance = await startTake(result);
    expect(await onUtterance(new Blob(), false)).toBe("applied");
    expect(apply).toHaveBeenCalledWith(take);
    expect(onError).not.toHaveBeenCalled();
    expect(voice.requestVoiceChart.mock.calls[0]?.[6]).toBe(false);
  });

  it("refuses a take that would overwrite typing done while it was charted", async () => {
    const song = { body: "before", chart: null };
    const apply = vi.fn();
    const onError = vi.fn();
    voice.requestVoiceChart.mockImplementation(async () => {
      song.body = "before, plus a line typed during the wait";
      return take;
    });
    const { result } = renderHook(() =>
      useVoiceChart({ enabled: true, slideEdits: false, read: () => ({ ...song }), apply, onError }),
    );
    const onUtterance = await startTake(result);
    expect(await onUtterance(new Blob(), false)).toBe("error");
    expect(apply).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(CHANGED_WHILE_CHARTING);
  });

  it("never opens the microphone when switched off", async () => {
    const { result } = renderHook(() =>
      useVoiceChart({ enabled: false, slideEdits: false, read: () => ({ body: "", chart: null }), apply: vi.fn(), onError: vi.fn() }),
    );
    result.current.warm();
    await act(async () => {
      await result.current.toggle();
    });
    expect(voice.warmVoiceInput).not.toHaveBeenCalled();
    expect(voice.startVoiceRecording).not.toHaveBeenCalled();
  });
});

function renderEditor() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <SongEditor songId={null} onClose={vi.fn()} />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

describe("the song editor's Voice button", () => {
  it("is hidden without a Groq key, and opening a song doesn't open the mic", () => {
    renderEditor();
    expect(screen.queryByTestId("voice-chart-btn")).toBeNull();
    expect(voice.warmVoiceInput).not.toHaveBeenCalled();
  });

  it("shows once a key is saved, and still waits for a press before opening the mic", () => {
    models.state = { ...models.state, models: [{ provider: "groq", has_api_key: true }] };
    renderEditor();
    expect(screen.getByTestId("voice-chart-btn")).toBeInTheDocument();
    expect(voice.warmVoiceInput).not.toHaveBeenCalled();
  });
});
