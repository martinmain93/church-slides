/** Small pieces the web app's pages share. Phone first: 44px touch targets,
 * one column, 16px side insets, the main action at the top right. */

import { Component, useCallback, useEffect, useId, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, Info, X } from "lucide-react";

import { useLibrary } from "@/library/context";
import type { LibrarySource } from "@/library/source";
import { cn } from "@/lib/utils";

/** Load something from the library, and again whenever it changes. A load
 * that finishes after a newer one started is dropped, and a failed one
 * shows as `error` rather than leaving the page blank. */
export function useLibraryData<T>(
  load: (lib: LibrarySource) => Promise<T>,
  deps: unknown[],
): { data: T | undefined; error: string | null; reload: () => void } {
  const lib = useLibrary();
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const latest = useRef(0);
  const reload = useCallback(() => {
    const n = ++latest.current;
    loadRef.current(lib).then(
      (d) => {
        if (n !== latest.current) return;
        setData(d);
        setError(null);
      },
      (err: unknown) => n === latest.current && setError(errorText(err)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lib, ...deps]);
  useEffect(() => {
    // Another page's data mustn't show while this one loads.
    setData(undefined);
    reload();
    return lib.onChange(reload);
  }, [lib, reload]);
  return { data, error, reload };
}

/** The page's name in the browser tab. */
export function useTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · Church Slides` : "Church Slides";
  }, [title]);
}

export const iconButtonClass =
  "flex size-11 shrink-0 items-center justify-center rounded-md text-foreground hover:bg-muted disabled:opacity-50";

export function PageHeader({
  title,
  back,
  action,
  wrap = false,
  below,
}: {
  title: ReactNode;
  back?: string;
  action?: ReactNode;
  /** Let a long title take two lines (detail pages) instead of cutting it. */
  wrap?: boolean;
  /** Something that stays with the header as the page scrolls (a search). */
  below?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background pt-[env(safe-area-inset-top)]">
      <div className={cn("flex min-h-14 items-center gap-1 py-1.5 pr-3", back ? "pl-1" : "pl-4")}>
        {back && (
          <a href={back} className={iconButtonClass} aria-label="Back">
            <ChevronLeft className="size-5" />
          </a>
        )}
        <h1 className={cn("min-w-0 flex-1 text-lg font-semibold leading-snug", wrap ? "line-clamp-2 break-words" : "truncate")}>{title}</h1>
        {action && <div className="flex shrink-0 items-center gap-1">{action}</div>}
      </div>
      {below && <div className="px-4 pb-3">{below}</div>}
    </header>
  );
}

export function PrimaryButton({ className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-11 items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export function QuietButton({ className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-11 items-center justify-center gap-1.5 rounded-md border border-border px-4 text-sm font-medium hover:bg-muted disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

/** Delete, remove: a full-width row in red that reads on the dark page. */
export function DangerButton({ className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        "flex h-11 w-full items-center justify-center gap-1.5 rounded-md border border-red-400/40 px-4 text-sm font-medium text-red-600 hover:bg-red-400/10 dark:text-red-400 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export const fieldClass =
  "h-11 w-full rounded-md border border-input bg-background px-3 text-base placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring aria-[invalid=true]:border-red-400";

export function Field({
  label,
  hint,
  info,
  error,
  children,
}: {
  label: string;
  /** One line under the field. */
  hint?: ReactNode;
  /** The detail behind an (i) beside the hint, shown on a tap. */
  info?: ReactNode;
  /** Shown under the field in red, above the hint (which still explains). */
  error?: string | null;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <label className="block space-y-1.5">
      <span className="block text-sm font-medium">{label}</span>
      {children}
      {error && (
        <span className="block text-sm text-red-600 dark:text-red-400" role="alert">
          {error}
        </span>
      )}
      {(hint || info) && (
        <span className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
          <span className="min-w-0 flex-1">{hint}</span>
          {info && (
            <button
              type="button"
              className="-my-2.5 -mr-2.5 flex size-10 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
              aria-label={`More about ${label}`}
              aria-expanded={open}
              onClick={(e) => {
                e.preventDefault();
                setOpen((o) => !o);
              }}
            >
              <Info className="size-4" />
            </button>
          )}
        </span>
      )}
      {info && open && <span className="block rounded-md bg-muted px-3 py-2 text-xs leading-relaxed text-muted-foreground">{info}</span>}
    </label>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-6 py-10 text-center text-sm text-muted-foreground">{children}</div>;
}

/** A line saying what went wrong, at the top of a page. */
export function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <div className="mx-4 mt-3 rounded-md border border-red-400/40 bg-red-400/10 px-3 py-2 text-sm" role="alert">
      {children}
    </div>
  );
}

/** Open sheets, the top one last: Escape closes only the top one. */
const openSheets: object[] = [];

/**
 * A panel that slides up from the bottom (phone) or sits centred (wider).
 * It's modal: the page behind can't be reached, focus moves into it and
 * goes back where it was when it closes.
 */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    const me = {};
    openSheets.push(me);
    root?.setAttribute("inert", "");
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && openSheets[openSheets.length - 1] === me && close.current();
    window.addEventListener("keydown", onKey);
    // The first field if there is one (autofocus may have taken it), else the panel.
    if (!panel.current?.contains(document.activeElement)) {
      const first = panel.current?.querySelector<HTMLElement>("[data-autofocus], input, textarea, select");
      (first ?? panel.current)?.focus();
    }
    return () => {
      window.removeEventListener("keydown", onKey);
      openSheets.splice(openSheets.indexOf(me), 1);
      if (openSheets.length === 0) root?.removeAttribute("inert");
      before?.focus?.();
    };
  }, []);
  return createPortal(
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-black/60 sm:items-center sm:p-4" onClick={() => close.current()}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="web-app flex max-h-[85dvh] w-full max-w-lg flex-col rounded-t-xl border-x border-t border-border bg-card pb-[env(safe-area-inset-bottom)] outline-none sm:rounded-xl sm:border sm:pb-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border py-1.5 pl-4 pr-1.5">
          <h2 id={titleId} className="min-w-0 truncate font-semibold">
            {title}
          </h2>
          <button type="button" className={iconButtonClass} onClick={() => close.current()} aria-label="Close">
            <X className="size-5" />
          </button>
        </div>
        <div className="overflow-y-auto p-4">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

/** "Delete this?": one line and a red action, or only OK when it can't be done. */
export function ConfirmSheet({
  title,
  children,
  action,
  onAction,
  onClose,
}: {
  title: string;
  children: ReactNode;
  /** The red button's words; none means it's only information. */
  action?: string;
  onAction?: () => void;
  onClose: () => void;
}) {
  return (
    <Sheet title={title} onClose={onClose}>
      <div className="space-y-4">
        <div className="text-sm leading-relaxed text-muted-foreground">{children}</div>
        <div className="flex flex-col gap-2">
          {action && <DangerButton onClick={onAction}>{action}</DangerButton>}
          <QuietButton className="w-full" onClick={onClose} data-autofocus>
            {action ? "Cancel" : "OK"}
          </QuietButton>
        </div>
      </div>
    </Sheet>
  );
}

/** Keeps one broken page (a malformed chart from another device, say) from
 * blanking the whole app. */
export class PageBoundary extends Component<{ children: ReactNode; resetKey: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error(err, info.componentStack);
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }
  render() {
    return this.state.failed ? <Empty>This page couldn&apos;t be shown. Go back and try another.</Empty> : this.props.children;
  }
}

/** Error text from a rejected promise, for a one-line message. */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
