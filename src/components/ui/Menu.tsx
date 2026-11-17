/**
 * A small dropdown menu: the "⋯" that keeps the less-used controls of a
 * screen one click away instead of in its header, and the arrow on the side
 * of a split button.
 *
 * Closes on outside click, on Escape and when an item is chosen. Escape is
 * caught before the presentation keymap sees it — in a service, Escape would
 * otherwise end the service.
 */

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CloseContext = createContext<() => void>(() => undefined);

export function Menu({
  children,
  trigger,
  align = "right",
  placement = "below",
  label = "More",
  className,
  panelClassName,
}: {
  children: ReactNode;
  /** A custom trigger; defaults to a "⋯" icon button. */
  trigger?: (toggle: () => void, open: boolean) => ReactNode;
  /** Which edge of the trigger the panel lines up with. */
  align?: "left" | "right";
  placement?: "below" | "above";
  /** Accessible name and tooltip of the default trigger. */
  label?: string;
  className?: string;
  panelClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

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

  const toggle = () => setOpen((v) => !v);

  return (
    <div className={cn("relative", className)} ref={ref}>
      {trigger ? (
        trigger(toggle, open)
      ) : (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={toggle}
          aria-label={label}
          aria-haspopup="menu"
          aria-expanded={open}
          title={label}
        >
          <MoreHorizontal className="size-4" />
        </Button>
      )}
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-50 min-w-[14rem] rounded-lg border border-border bg-background p-1 text-sm text-foreground shadow-xl",
            placement === "above" ? "bottom-full mb-1" : "top-full mt-1",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          <CloseContext.Provider value={() => setOpen(false)}>{children}</CloseContext.Provider>
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  icon,
  children,
  hint,
  onSelect,
  destructive,
  disabled,
  checked,
}: {
  icon?: ReactNode;
  children: ReactNode;
  /** A second, quieter line under the label. */
  hint?: ReactNode;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
  /** Renders as a radio item when set. */
  checked?: boolean;
}) {
  const close = useContext(CloseContext);
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemradio"}
      aria-checked={checked}
      disabled={disabled}
      onClick={(e) => {
        // Hand focus back: a focused button would swallow the arrow keys and
        // space bar a service is driven with.
        e.currentTarget.blur();
        close();
        onSelect();
      }}
      className={cn(
        "flex w-full items-start gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50",
        destructive && "text-red-400 hover:bg-red-500/10",
        checked && "bg-accent/60",
      )}
    >
      {icon && <span className="mt-0.5 shrink-0 text-muted-foreground [&_svg]:size-4">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block">{children}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </button>
  );
}

export function MenuSeparator() {
  return <div className="my-1 h-px bg-border" role="separator" />;
}
