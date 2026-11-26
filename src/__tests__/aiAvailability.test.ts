import { describe, expect, it } from "vitest";

import { availabilitySummary, describeAiAvailability } from "@/lib/aiAvailability";
import type { ModelDownloadEvent } from "@/lib/modelDownload";
import type { ModelStatusResponse } from "@/types/generated";

const ready = { state: "ready" as const, port: 1 };

function model(over: Partial<ModelStatusResponse> = {}): ModelStatusResponse {
  return {
    kind: "model_status_response",
    backend: "whisper",
    model_size: "small.en",
    downloaded: false,
    download_mb: 480,
    needs_internet_while_running: false,
    ...over,
  };
}

function progress(state: ModelDownloadEvent["state"], over: Partial<ModelDownloadEvent> = {}) {
  return {
    model_size: "small.en",
    bytes_downloaded: 0,
    bytes_total: 1,
    fraction: 0,
    state,
    detail: "",
    received_at_ms: 0,
    ...over,
  };
}

describe("describeAiAvailability", () => {
  it("says why when there's no engine, instead of 'starting' forever", () => {
    const a = describeAiAvailability({ state: "failed", reason: "built without AI" }, null, null);
    expect(a).toEqual({ kind: "engine_unavailable", reason: "built without AI" });
  });

  it("names the download and that it needs internet once", () => {
    const a = describeAiAvailability(ready, model(), null);
    expect(a).toEqual({ kind: "needs_download", model: "small.en", mb: 480 });
    expect(availabilitySummary(a)).toMatch(/internet connection once/);
    expect(availabilitySummary(a)).toMatch(/480 MB/);
  });

  it("is ready offline once the model is on disk", () => {
    const a = describeAiAvailability(ready, model({ downloaded: true, download_mb: 0 }), null);
    expect(a).toEqual({ kind: "ready", model: "small.en", hosted: false });
    expect(availabilitySummary(a)).toMatch(/works offline/);
  });

  it("flags hosted transcription as needing internet all service", () => {
    const a = describeAiAvailability(
      ready,
      model({ backend: "groq", model_size: "whisper-large-v3-turbo", needs_internet_while_running: true }),
      null,
    );
    expect(a).toMatchObject({ kind: "ready", hosted: true });
    expect(availabilitySummary(a)).toMatch(/whole service/);
  });

  it("shows a download in flight, and the reason it failed", () => {
    expect(describeAiAvailability(ready, model(), progress("starting")).kind).toBe("downloading");
    const failed = describeAiAvailability(
      ready,
      model(),
      progress("failed", { detail: "Couldn't download … needs an internet connection" }),
    );
    expect(failed).toMatchObject({ kind: "download_failed" });
    expect(availabilitySummary(failed)).toMatch(/internet connection/);
  });

  it("ignores progress for a different model size", () => {
    const a = describeAiAvailability(ready, model(), progress("failed", { model_size: "tiny.en" }));
    expect(a.kind).toBe("needs_download");
  });

  it("waits for the engine before judging the model", () => {
    expect(describeAiAvailability(null, null, null).kind).toBe("checking");
    expect(describeAiAvailability({ state: "starting" }, model(), null).kind).toBe("checking");
    // The engine is up and only the model check is outstanding: the engine
    // row must not read "Checking…" as if the engine itself were in doubt.
    expect(describeAiAvailability(ready, null, null).kind).toBe("checking_model");
  });

  it("counts the megabytes as a download arrives", () => {
    const a = describeAiAvailability(
      ready,
      model(),
      progress("downloading", { bytes_downloaded: 120 * 1024 * 1024 }),
    );
    expect(a).toMatchObject({ kind: "downloading", mbDone: 120 });
    expect(availabilitySummary(a)).toMatch(/120 MB of about 480 MB/);
  });

  it("says a download over a gigabyte in GB, and names no model", () => {
    const a = describeAiAvailability(ready, model({ download_mb: 2259 }), null);
    const text = availabilitySummary(a);
    expect(text).toMatch(/about 2\.2 GB/);
    expect(text).not.toMatch(/small\.en|whisper|qwen/i);
  });
});

describe("the machine check (off Apple silicon)", () => {
  const here = model({ downloaded: true });

  it("is ready only once this computer passed", () => {
    expect(describeAiAvailability(ready, here, null, { state: "running" }).kind).toBe(
      "checking_machine",
    );
    const passed = describeAiAvailability(ready, here, null, {
      state: "done",
      verdict: "full",
      reason: "Keeps up",
    });
    expect(passed).toMatchObject({ kind: "ready", checked: true });
    expect(availabilitySummary(passed)).toMatch(/keeps up with live AI/);
  });

  it("offers Assist alone on a computer that passed only Assist's bar", () => {
    const a = describeAiAvailability(ready, here, null, {
      state: "done",
      verdict: "assist",
      reason: "Too slow for the AI to change slides itself, but fast enough for Assist.",
    });
    expect(a).toMatchObject({ kind: "ready", checked: true, assistOnly: true });
    const text = availabilitySummary(a);
    expect(text).toMatch(/Ready for Assist/);
    expect(text).toMatch(/not with it changing slides itself/);
  });

  it("turns live AI off on a computer too slow for it, and says why", () => {
    const slow = describeAiAvailability(ready, here, null, {
      state: "done",
      verdict: "insufficient",
      reason: "Too slow for live following at full accuracy.",
    });
    expect(slow.kind).toBe("too_slow");
    const text = availabilitySummary(slow);
    expect(text).toMatch(/Too slow/);
    expect(text).toMatch(/Presenting by hand works/);
  });

  it("says no before offering any download: live AI can't run here", () => {
    const missing = model({ downloaded: false });
    const no = describeAiAvailability(ready, missing, null, {
      state: "done",
      verdict: "insufficient",
      reason: "Live AI needs a Mac with Apple silicon (M1 or newer) on macOS 14 or later.",
    });
    expect(no.kind).toBe("too_slow");
    expect(describeAiAvailability(ready, missing, null, { state: "running" }).kind).toBe(
      "checking_machine",
    );
  });

  it("asks for another check when it couldn't judge", () => {
    for (const machine of [
      { state: "done" as const, verdict: "unknown" as const, reason: "Low Power Mode is on." },
      { state: "failed" as const, reason: "The check couldn't run: timed out" },
    ]) {
      expect(describeAiAvailability(ready, here, null, machine).kind).toBe("check_unavailable");
    }
  });

  it("isn't asked for when no check is needed (Apple silicon, hosted speech)", () => {
    expect(describeAiAvailability(ready, here, null)).toEqual({
      kind: "ready",
      model: "small.en",
      hosted: false,
    });
  });
});
