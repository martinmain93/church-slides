/**
 * Tiny toast notification system. Context-based — `useToast()` returns
 * imperative methods, `<ToastViewport />` renders the queue. No deps.
 *
 * Usage:
 *   const toast = useToast();
 *   toast.success("Library exported");
 *   toast.error("Save failed: …");
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

export type ToastVariant = "info" | "success" | "error";

interface Toast {
  id: number;
  message: string;
  variant: ToastVariant;
  /** ms; 0 = sticky until dismissed */
  duration: number;
}

interface ToastApi {
  show(message: string, opts?: { variant?: ToastVariant; duration?: number }): number;
  success(message: string, duration?: number): number;
  error(message: string, duration?: number): number;
  dismiss(id: number): void;
}

const ToastContext = createContext<ToastApi | null>(null);

interface ProviderProps {
  children: ReactNode;
}

export function ToastProvider({ children }: ProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback<ToastApi["show"]>(
    (message, opts) => {
      const id = idRef.current++;
      const toast: Toast = {
        id,
        message,
        variant: opts?.variant ?? "info",
        duration: opts?.duration ?? 4000,
      };
      setToasts((prev) => [...prev, toast]);
      if (toast.duration > 0) {
        setTimeout(() => dismiss(id), toast.duration);
      }
      return id;
    },
    [dismiss],
  );

  const api: ToastApi = {
    show,
    success: (m, d) => show(m, { variant: "success", duration: d }),
    error: (m, d) => show(m, { variant: "error", duration: d ?? 6000 }),
    dismiss,
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/** For components that also render outside the app shell (tests, other
 * windows): toasts when there's somewhere to show them, null otherwise. */
export function useOptionalToast(): ToastApi | null {
  return useContext(ToastContext);
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used inside a ToastProvider");
  }
  return ctx;
}

interface ViewportProps {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}

function ToastViewport({ toasts, onDismiss }: ViewportProps) {
  return (
    <div
      role="region"
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  // Brief slide-in
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setShown(true), 10);
    return () => window.clearTimeout(id);
  }, []);

  const variantClasses: Record<ToastVariant, string> = {
    info: "border-border bg-card text-card-foreground",
    success: "border-emerald-500/40 bg-emerald-500/15 text-emerald-100",
    error: "border-rose-500/40 bg-rose-500/15 text-rose-100",
  };

  return (
    <div
      role="status"
      data-testid="toast"
      data-variant={toast.variant}
      className={cn(
        "pointer-events-auto rounded-md border px-4 py-3 text-sm shadow-md transition-all",
        variantClasses[toast.variant],
        shown ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="leading-snug">{toast.message}</span>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs opacity-60 hover:opacity-100"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>
    </div>
  );
}
