/**
 * `useConfirm()` — promise-returning replacement for `window.confirm()`.
 *
 * Tauri's WKWebView does not implement the synchronous browser
 * `confirm()` / `alert()` / `prompt()` APIs. Calls return `false`
 * silently with no UI shown, which silently kills destructive flows
 * (delete buttons, import-replace-vs-merge, etc.). This hook renders
 * a real modal and returns a Promise<boolean>, so existing callers
 * can `await` the answer in one line.
 *
 * Usage:
 *
 *   const confirm = useConfirm();
 *   const ok = await confirm({
 *     title: "Delete song?",
 *     message: "This can't be undone.",
 *     confirmLabel: "Delete",
 *     variant: "destructive",
 *   });
 *   if (!ok) return;
 *
 * The provider is mounted once at the app root (`AppShell`) and
 * sits alongside the toast provider.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ConfirmVariant = "default" | "destructive";

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
}

interface ConfirmRequest extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(
  null,
);

interface ProviderProps {
  children: ReactNode;
}

export function ConfirmProvider({ children }: ProviderProps) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      setRequest({ ...opts, resolve });
    });
  }, []);

  function handleAnswer(ok: boolean) {
    if (request) {
      request.resolve(ok);
      setRequest(null);
    }
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && (
        <ConfirmDialog request={request} onAnswer={handleAnswer} />
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (opts: ConfirmOptions) => Promise<boolean> {
  const fn = useContext(ConfirmContext);
  if (!fn) {
    // Soft fallback so unit tests that don't wrap with the provider
    // don't blow up. Returning `true` matches the "user clicked OK"
    // happy path most tests want.
    return async () => true;
  }
  return fn;
}


function ConfirmDialog({
  request,
  onAnswer,
}: {
  request: ConfirmRequest;
  onAnswer: (ok: boolean) => void;
}) {
  // Escape cancels; Enter confirms.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        onAnswer(false);
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        onAnswer(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onAnswer]);

  const variant = request.variant ?? "default";
  const confirmLabel = request.confirmLabel ?? "OK";
  const cancelLabel = request.cancelLabel ?? "Cancel";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby={request.title ? "confirm-dialog-title" : undefined}
      onClick={(ev) => {
        // Click outside the panel cancels.
        if (ev.target === ev.currentTarget) onAnswer(false);
      }}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-2xl"
        role="document"
      >
        {request.title && (
          <h2
            id="confirm-dialog-title"
            className="mb-2 text-base font-semibold text-foreground"
          >
            {request.title}
          </h2>
        )}
        <p className="whitespace-pre-line text-sm text-muted-foreground">
          {request.message}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onAnswer(false)}
            autoFocus={variant === "destructive"}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={variant === "destructive" ? "destructive" : "default"}
            onClick={() => onAnswer(true)}
            className={cn(variant === "destructive" && "min-w-[5rem]")}
            autoFocus={variant !== "destructive"}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
