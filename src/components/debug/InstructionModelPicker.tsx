/**
 * Which model spoken instructions runs on — local (download + select) or
 * hosted (a key + select). Fetches the catalog on mount and re-fetches
 * whenever a download finishes, so `installed`/`has_api_key` stay current
 * without the operator having to reopen the tab.
 */

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import {
  downloadInstructionModel,
  refreshInstructionModels,
  setInstructionModel,
  setInstructionModelApiKey,
  useInstructionModelDownload,
  useInstructionModels,
} from "@/lib/instructionModels";

export function InstructionModelPicker() {
  const models = useInstructionModels();
  const download = useInstructionModelDownload();
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  // Distinct from the store's `seen` flag: a rejected request (no sidecar —
  // browser dev mode, or the connection dropped) must not leave this stuck
  // on "loading" forever, and must not throw an unhandled rejection either.
  const [refreshError, setRefreshError] = useState<string | null>(null);

  function refresh() {
    refreshInstructionModels()
      .then(() => setRefreshError(null))
      .catch((err: unknown) => {
        setRefreshError(err instanceof Error ? err.message : String(err));
      });
  }

  useEffect(() => {
    refresh();
  }, []);

  // The download request resolves as soon as the server acks it — long
  // before the download itself finishes — so completion has to be read off
  // the broadcast, not the request promise. See `downloadInstructionModel`.
  useEffect(() => {
    if (download && (download.state === "completed" || download.state === "failed")) {
      refresh();
    }
  }, [download?.state, download?.modelId]);

  if (refreshError) {
    return (
      <p className="mb-3 text-zinc-500">
        Couldn&rsquo;t reach the sidecar to list models ({refreshError}).
      </p>
    );
  }

  if (!models.seen) {
    return <p className="mb-3 text-zinc-500">loading models…</p>;
  }

  const local = models.models.filter((m) => m.model_kind === "local");
  const hosted = models.models.filter((m) => m.model_kind === "hosted");

  return (
    <div className="mb-3 flex flex-col gap-2">
      {!models.ollamaAvailable && (
        <div className="rounded border border-white/10 bg-white/5 px-2 py-2 text-zinc-400">
          <p className="mb-1">
            Ollama isn't running — needed for any local model below.
          </p>
          <pre className="mb-1 overflow-x-auto rounded bg-black/40 px-2 py-1.5 text-[11px] text-zinc-200">
            brew install ollama{"\n"}ollama serve
          </pre>
          <p>The hosted models further down still work without it, given a key.</p>
        </div>
      )}

      <div className="font-sans text-[11px] uppercase tracking-wide text-zinc-500">
        Local
      </div>
      {local.map((m) => (
        <div
          key={m.id}
          className={cn(
            "flex items-start gap-2 rounded border px-2 py-1.5",
            m.id === models.activeModelId
              ? "border-emerald-500/40 bg-emerald-500/10"
              : "border-white/10 bg-white/5",
          )}
        >
          <input
            type="radio"
            className="mt-0.5"
            checked={m.id === models.activeModelId}
            disabled={!m.installed}
            onChange={() => setInstructionModel(m.id).catch(console.error)}
          />
          <div className="flex-1 font-sans leading-tight">
            <div className="flex items-center gap-2">
              <span className="text-zinc-200">{m.label}</span>
              {m.size_mb ? (
                <span className="text-[10px] text-zinc-500">
                  {(m.size_mb / 1000).toFixed(1)}GB
                </span>
              ) : null}
            </div>
            <div className="text-[10px] text-zinc-400">{m.note}</div>
            {!m.installed && download?.modelId === m.id ? (
              <div className="mt-1">
                <div className="h-1 overflow-hidden rounded bg-white/10">
                  <div
                    className={cn(
                      "h-full",
                      download.state === "failed" ? "bg-red-500" : "bg-sky-500",
                    )}
                    style={{ width: `${Math.round(download.fraction * 100)}%` }}
                  />
                </div>
                <div className="mt-0.5 text-[10px] text-zinc-500">
                  {download.state === "failed" ? `failed: ${download.detail}` : download.detail}
                </div>
              </div>
            ) : !m.installed ? (
              <button
                type="button"
                className="mt-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-white/10"
                onClick={() => downloadInstructionModel(m.id).catch(console.error)}
              >
                Download
              </button>
            ) : null}
          </div>
        </div>
      ))}

      <div className="mt-1 font-sans text-[11px] uppercase tracking-wide text-zinc-500">
        Hosted — needs a network connection and a key
      </div>
      {hosted.map((m) => {
        // Hosted catalog entries always carry a provider — only local
        // entries leave it blank — but the generated type is optional
        // because the Python field has a default. Fall back defensively.
        const provider = m.provider ?? "";
        return (
          <div
            key={m.id}
            className={cn(
              "flex items-start gap-2 rounded border px-2 py-1.5",
              m.id === models.activeModelId
                ? "border-emerald-500/40 bg-emerald-500/10"
                : "border-white/10 bg-white/5",
            )}
          >
            <input
              type="radio"
              className="mt-0.5"
              checked={m.id === models.activeModelId}
              disabled={!m.has_api_key}
              onChange={() => setInstructionModel(m.id).catch(console.error)}
            />
            <div className="flex-1 font-sans leading-tight">
              <span className="text-zinc-200">{m.label}</span>
              <div className="text-[10px] text-zinc-400">{m.note}</div>
              <div className="mt-1 flex items-center gap-1.5">
                <input
                  type="password"
                  placeholder={
                    m.has_api_key ? "key on file — enter a new one to replace" : "API key"
                  }
                  className="w-full rounded border border-white/10 bg-black/30 px-1.5 py-0.5 text-[11px] text-zinc-200"
                  value={keyDrafts[provider] ?? ""}
                  onChange={(e) =>
                    setKeyDrafts((prev) => ({ ...prev, [provider]: e.target.value }))
                  }
                />
                <button
                  type="button"
                  className="shrink-0 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-white/10"
                  onClick={() => {
                    const key = keyDrafts[provider] ?? "";
                    setInstructionModelApiKey(provider, key).catch(console.error);
                    setKeyDrafts((prev) => ({ ...prev, [provider]: "" }));
                  }}
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
