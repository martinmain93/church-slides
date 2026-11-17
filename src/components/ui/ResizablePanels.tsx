/**
 * Lightweight horizontal resizable panel group.
 *
 * Renders its children side by side with draggable dividers between them.
 * Exactly one child is the "flex" panel (it absorbs remaining space); the
 * others have an explicit pixel width the operator can drag to adjust. The
 * divider shows a col-resize (↔) cursor on hover and persists sizes to
 * localStorage when a `storageKey` is given.
 *
 * Deliberately dependency-free (no react-resizable-panels): the app only
 * needs simple left/right splits and this keeps the bundle lean.
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

interface Props {
  /** Panels, in order. Exactly one (at `flexIndex`) flexes. */
  children: ReactNode[];
  /** Index of the panel that absorbs remaining width. */
  flexIndex: number;
  /** Initial pixel widths per panel. The flex panel's entry is ignored. */
  initialSizes: number[];
  /** Minimum pixel width per panel (defaults to 120). */
  minSizes?: number[];
  /** When set, sizes persist across sessions under this localStorage key. */
  storageKey?: string;
  /** Panels shrunk to their own content (e.g. a thin rail), with no divider. */
  collapsed?: boolean[];
  className?: string;
}

export function ResizablePanels({
  children,
  flexIndex,
  initialSizes,
  minSizes,
  storageKey,
  collapsed,
  className,
}: Props) {
  const [sizes, setSizes] = useState<number[]>(() => {
    if (storageKey) {
      try {
        const raw = localStorage.getItem(storageKey);
        if (raw) {
          const parsed = JSON.parse(raw);
          if (Array.isArray(parsed) && parsed.length === initialSizes.length) {
            return parsed as number[];
          }
        }
      } catch {
        // fall through to defaults
      }
    }
    return initialSizes;
  });

  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(sizes));
    } catch {
      // storage full / unavailable — non-fatal
    }
  }, [sizes, storageKey]);

  const minOf = useCallback(
    (i: number) => minSizes?.[i] ?? 120,
    [minSizes],
  );

  // `dividerIndex` sits between panel `dividerIndex` and `dividerIndex + 1`.
  // Whichever side is NOT the flex panel is the one we resize; the flex panel
  // takes up the slack.
  const adjust = useCallback(
    (dividerIndex: number, dx: number) => {
      setSizes((prev) => {
        const next = [...prev];
        if (dividerIndex !== flexIndex) {
          next[dividerIndex] = Math.max(
            minOf(dividerIndex),
            (prev[dividerIndex] ?? 0) + dx,
          );
        } else {
          const right = dividerIndex + 1;
          next[right] = Math.max(minOf(right), (prev[right] ?? 0) - dx);
        }
        return next;
      });
    },
    [flexIndex, minOf],
  );

  return (
    <div className={cn("flex h-full w-full overflow-hidden", className)}>
      {children.map((child, i) => (
        <Fragment key={i}>
          <div
            className="h-full overflow-hidden"
            style={
              i === flexIndex
                ? { flex: "1 1 0%", minWidth: minOf(i) }
                : collapsed?.[i]
                  ? { flexShrink: 0 }
                  : { width: sizes[i], flexShrink: 0 }
            }
          >
            {child}
          </div>
          {i < children.length - 1 && !collapsed?.[i] && !collapsed?.[i + 1] && (
            <Divider onDrag={(dx) => adjust(i, dx)} />
          )}
        </Fragment>
      ))}
    </div>
  );
}

function Divider({ onDrag }: { onDrag: (dx: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const lastX = useRef(0);
  const dragging = useRef(false);

  return (
    <div
      ref={ref}
      role="separator"
      aria-orientation="vertical"
      onPointerDown={(e) => {
        dragging.current = true;
        lastX.current = e.clientX;
        ref.current?.setPointerCapture(e.pointerId);
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        if (!dragging.current) return;
        const dx = e.clientX - lastX.current;
        lastX.current = e.clientX;
        if (dx !== 0) onDrag(dx);
      }}
      onPointerUp={(e) => {
        dragging.current = false;
        ref.current?.releasePointerCapture(e.pointerId);
      }}
      className="h-full w-1.5 shrink-0 cursor-col-resize touch-none select-none bg-border/60 transition-colors hover:bg-primary/60 active:bg-primary"
    />
  );
}
