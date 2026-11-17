/**
 * An (i) beside a label that opens a short explanation.
 *
 * For the detail a setting needs but most people don't: what exactly a
 * report contains, why a choice is recommended. The label's own hint stays
 * one short line; the rest lives here, one click away.
 *
 * Click (or Enter/Space) to open, click outside or Escape to close — the same
 * rules as `Menu`, and Escape is caught before anything else sees it.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

export function InfoTip({
  children,
  label = "More information",
  align = "left",
  className,
}: {
  children: ReactNode;
  /** Accessible name of the (i) button. */
  label?: string;
  /** Which edge of the button the panel lines up with. */
  align?: "left" | "right";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <span ref={ref} className={cn("relative inline-flex align-middle", className)}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={(e) => {
          // Inside a <label> or a clickable row, the (i) is not that control.
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={cn(
          "inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground",
          open && "text-foreground",
        )}
      >
        <Info className="size-3.5" />
      </button>
      {open && (
        <span
          id={panelId}
          role="note"
          className={cn(
            "absolute top-full z-50 mt-1.5 block w-72 rounded-md border border-border bg-popover p-3 text-xs font-normal leading-relaxed text-popover-foreground shadow-lg",
            align === "left" ? "left-0" : "right-0",
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
