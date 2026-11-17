/**
 * `useAiAvailability()` — can live AI run here, and if not, what does it need?
 *
 * Everything except live AI works without the engine, without a model, and
 * without internet: the library, presenting by hand, the band screens. So the
 * operator is never blocked by any of this; they need to know, *before* a
 * service, whether AI will be there and what to do if it won't. That used to
 * surface as "AI: starting" forever (no engine), or as a session that failed
 * to start with the reason in the console (no model, no internet).
 *
 * Three sources, combined by `describeAiAvailability`:
 *   - the shell's engine state (`get_ai_engine_status`) — polled, because the
 *     engine can also stop mid-service;
 *   - `model_status` for the backend/model a service will actually use;
 *   - `model_download_progress` broadcasts, for a download in flight.
 */

import { useCallback, useEffect, useState } from "react";

import { loadAudioInputPrefs, resolveLiveAsr } from "@/lib/audioInputPrefs";
import { type ModelDownloadEvent, useModelDownloadProgress } from "@/lib/modelDownload";
import { requestSidecar } from "@/lib/sidecarClient";
import { type AiEngineStatus, getAiEngineStatus } from "@/lib/tauri";
import type { ModelStatusResponse } from "@/types/generated";

export type AiAvailability =
  | { kind: "checking" }
  /** The engine is up; asking it whether the model is on this computer. */
  | { kind: "checking_model" }
  /** No engine: not in this build, can't start here, or it stopped. */
  | { kind: "engine_unavailable"; reason: string }
  /** Works once the model is downloaded, which needs internet once. */
  | { kind: "needs_download"; model: string; mb: number }
  /** `mbDone` is null until the first progress report arrives. */
  | { kind: "downloading"; model: string; mb: number; mbDone: number | null }
  | { kind: "download_failed"; model: string; message: string }
  /** Off Apple silicon: timing live following's work on this computer
   * (the engine's machine check, ~30 s the first time). */
  | { kind: "checking_machine" }
  /** The check found this computer too slow for live AI at full accuracy,
   * so live AI isn't offered here (presenting by hand is unaffected). */
  | { kind: "too_slow"; reason: string }
  /** The check couldn't judge (Low Power Mode, say): `reason` says why. */
  | { kind: "check_unavailable"; reason: string }
  /** Ready. `hosted` means it needs internet for the whole service;
   * `checked`, that this computer passed its machine check (off Apple silicon);
   * `assistOnly`, that it passed only Assist's lighter bar: the AI may point
   * out where the band went (operator view), never change slides itself. */
  | { kind: "ready"; model: string; hosted: boolean; checked?: boolean; assistOnly?: boolean };

/** The machine check's verdict (`assess_machine`), as the hook keeps it. */
export type MachineVerdict =
  | { state: "not_needed" }
  | { state: "running" }
  | { state: "done"; verdict: "full" | "assist" | "insufficient" | "unknown"; reason: string }
  | { state: "failed"; reason: string };

const POLL_MS = 3000;
/** A first-time download of the largest model on a slow connection. */
const PREPARE_TIMEOUT_MS = 60 * 60 * 1000;

export function describeAiAvailability(
  engine: AiEngineStatus | null,
  model: ModelStatusResponse | null,
  progress: ModelDownloadEvent | null,
  machine: MachineVerdict = { state: "not_needed" },
): AiAvailability {
  if (!engine || engine.state === "starting") return { kind: "checking" };
  if (engine.state === "failed") return { kind: "engine_unavailable", reason: engine.reason };
  if (!model) return { kind: "checking_model" };

  if (model.needs_internet_while_running) {
    return { kind: "ready", model: model.model_size, hosted: true };
  }
  if (model.downloaded) {
    // Off Apple silicon the computer itself has to keep up: the engine's
    // machine check decides, and below its bar live AI isn't offered at all.
    switch (machine.state) {
      case "running":
        return { kind: "checking_machine" };
      case "failed":
        return { kind: "check_unavailable", reason: machine.reason };
      case "done":
        if (machine.verdict === "insufficient") return { kind: "too_slow", reason: machine.reason };
        if (machine.verdict === "unknown") {
          return { kind: "check_unavailable", reason: machine.reason };
        }
        if (machine.verdict === "assist") {
          return { kind: "ready", model: model.model_size, hosted: false, checked: true, assistOnly: true };
        }
        return { kind: "ready", model: model.model_size, hosted: false, checked: true };
    }
    return { kind: "ready", model: model.model_size, hosted: false };
  }
  // Only this model's download counts; a progress event left over from
  // another size says nothing about this one.
  const mine = progress && progress.model_size === model.model_size ? progress : null;
  if (mine?.state === "starting" || mine?.state === "downloading") {
    const mbDone = mine.state === "downloading" ? Math.round(mine.bytes_downloaded / (1024 * 1024)) : null;
    return { kind: "downloading", model: model.model_size, mb: model.download_mb, mbDone };
  }
  if (mine?.state === "failed") {
    return { kind: "download_failed", model: model.model_size, message: mine.detail };
  }
  return { kind: "needs_download", model: model.model_size, mb: model.download_mb };
}

export interface AiAvailabilityApi {
  availability: AiAvailability;
  /** Download (and load) the model now, e.g. at home before a service. */
  prepare(): Promise<void>;
  /** Run this computer's machine check again (Settings → "Check again"). */
  recheck(): Promise<void>;
}

/** The first check times seconds of transcription and scoring on a CPU. */
const MACHINE_CHECK_TIMEOUT_MS = 3 * 60 * 1000;

export function useAiAvailability(): AiAvailabilityApi {
  const [engine, setEngine] = useState<AiEngineStatus | null>(null);
  const [model, setModel] = useState<ModelStatusResponse | null>(null);
  const [checkNonce, setCheckNonce] = useState(0);
  const [machine, setMachine] = useState<MachineVerdict>({ state: "not_needed" });
  const [machineNonce, setMachineNonce] = useState(0);
  const { latest } = useModelDownloadProgress();

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getAiEngineStatus()
        .then((s) => {
          if (cancelled) return;
          // Only re-render on an actual change — this runs every few seconds.
          setEngine((prev) => (JSON.stringify(prev) === JSON.stringify(s) ? prev : s));
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setEngine({ state: "failed", reason: err instanceof Error ? err.message : String(err) });
        });
    };
    poll();
    const id = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const engineReady = engine?.state === "ready";
  // Re-check the model whenever the engine comes up and after any download
  // finishes or fails, since that is when "needs download" turns into
  // "ready" (or an interrupted download back into "needs download").
  const downloadSettled =
    latest?.state === "completed" || latest?.state === "failed" ? latest.received_at_ms : 0;
  useEffect(() => {
    if (!engineReady) {
      setModel(null);
      return;
    }
    let cancelled = false;
    let retry: number | undefined;
    const check = async () => {
      try {
        const asr = await resolveLiveAsr(loadAudioInputPrefs());
        const status = await requestSidecar({
          kind: "model_status",
          backend: asr.backend,
          model_size: asr.model,
        });
        if (!cancelled) setModel(status);
      } catch {
        // The engine is up but didn't answer. Ask again: this used to wait
        // for "the next change", which never came, so one slow answer left
        // Settings on "Checking…" until the app was restarted.
        if (!cancelled) retry = window.setTimeout(() => void check(), POLL_MS);
      }
    };
    void check();
    return () => {
      cancelled = true;
      window.clearTimeout(retry);
    };
  }, [engineReady, downloadSettled, checkNonce]);

  const prepare = useCallback(async () => {
    const asr = await resolveLiveAsr(loadAudioInputPrefs());
    try {
      await requestSidecar(
        { kind: "prepare_model", backend: asr.backend, model_size: asr.model },
        { timeoutMs: PREPARE_TIMEOUT_MS },
      );
    } finally {
      setCheckNonce((n) => n + 1);
    }
  }, []);

  // Off Apple silicon, once the speech model is here: is this computer fast
  // enough? The engine keeps the verdict, so this is instant after the first
  // time. Apple silicon (mlx) and hosted speech (groq) don't need it.
  const needsCheck = model?.downloaded === true && model.backend === "whisper";
  useEffect(() => {
    if (!needsCheck) {
      setMachine({ state: "not_needed" });
      return;
    }
    let cancelled = false;
    setMachine({ state: "running" });
    requestSidecar(
      { kind: "assess_machine", rerun: machineNonce > 0 },
      { timeoutMs: MACHINE_CHECK_TIMEOUT_MS },
    )
      .then((v) => {
        if (!cancelled) setMachine({ state: "done", verdict: v.verdict, reason: v.reason });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason = err instanceof Error ? err.message : String(err);
        setMachine({ state: "failed", reason: `The check couldn't run: ${reason}` });
      });
    return () => {
      cancelled = true;
    };
  }, [needsCheck, machineNonce]);

  const recheck = useCallback(async () => {
    setMachineNonce((n) => n + 1);
  }, []);

  return {
    availability: describeAiAvailability(engine, model, latest, machine),
    prepare,
    recheck,
  };
}

/** One line for the operator: what live AI needs, or that it's ready. */
export function availabilitySummary(a: AiAvailability): string {
  switch (a.kind) {
    case "checking":
      return "Checking whether live AI can run on this computer…";
    case "checking_model":
      return "Checking whether the speech model is on this computer…";
    case "engine_unavailable":
      return a.reason;
    case "needs_download":
      return `Live AI needs its speech model (${a.model}, about ${a.mb} MB). Downloading it needs an internet connection once; after that it works offline.`;
    case "downloading":
      return a.mbDone === null
        ? `Downloading the speech model (${a.model}, about ${a.mb} MB)…`
        : `Downloading the speech model (${a.model}): ${Math.min(a.mbDone, a.mb)} of about ${a.mb} MB…`;
    case "download_failed":
      return a.message;
    case "checking_machine":
      return "Checking this computer can keep up with live AI (about half a minute, once)…";
    case "too_slow":
      return (
        `${a.reason} Live AI is off on this computer so it's never less accurate than it ` +
        "should be. Presenting by hand works as always, and a cloud option for slower " +
        "computers is planned."
      );
    case "check_unavailable":
      return a.reason;
    case "ready":
      if (a.assistOnly) {
        return (
          "Ready for Assist — this computer keeps up with the AI pointing out where the band " +
          "has gone, in the operator view, but not with it changing slides itself. You " +
          "change the slides."
        );
      }
      if (a.checked) {
        return `Ready — this computer keeps up with live AI, and the speech model (${a.model}) is on it, so it works offline.`;
      }
      return a.hosted
        ? `Ready, using hosted transcription (${a.model}) — it needs internet for the whole service.`
        : `Ready — the speech model (${a.model}) is on this computer, so live AI works offline.`;
  }
}
