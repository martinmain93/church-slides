/**
 * A song's key in a setlist, and the way to play it in another key for that
 * service only: the key badge opens twelve keys and "Original key". The band
 * and leader screens transpose the chart to the chosen key; the library song
 * keeps its own.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { KeyBadge } from "@/components/KeyBadge";
import { KEY_CHOICES, majorTonicPitchClass, transposeKey } from "@/lib/chords/chord";
import { cn } from "@/lib/utils";

function isMinor(key: string): boolean {
  return /^[A-G][#b]?m$/.test(key);
}

export function ServiceKeyBadge({
  originalKey,
  serviceKey,
  onChange,
  className = "size-5 text-[10px]",
}: {
  /** The chart's key, or null when the song has no chart (or no key on it). */
  originalKey: string | null;
  /** The key chosen for this service, or null for the original. */
  serviceKey: string | null;
  onChange: (key: string | null) => void;
  className?: string;
}) {
  const shown = serviceKey ?? originalKey;
  const changed = serviceKey !== null && originalKey !== null && serviceKey !== originalKey;
  const title = !shown
    ? "Set the key for this service"
    : changed
      ? `In ${serviceKey} for this service (original key ${originalKey}) — click to change`
      : `Key of ${shown} — click to play it in another key for this service`;
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const open = anchor !== null;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panelRef.current?.contains(t) && !buttonRef.current?.contains(t)) setAnchor(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // In a service, Escape would otherwise end it.
      e.stopPropagation();
      setAnchor(null);
    };
    const onScroll = () => setAnchor(null);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={(e) => {
          // The badge sits on rows that select or drag on click.
          e.stopPropagation();
          setAnchor(open ? null : e.currentTarget.getBoundingClientRect());
        }}
        onPointerDown={(e) => e.stopPropagation()}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={title}
        title={title}
        data-testid="service-key"
        className={cn(
          "flex shrink-0 self-center rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          changed && "ring-2 ring-foreground/70 ring-offset-1 ring-offset-background",
        )}
      >
        {shown ? (
          <KeyBadge songKey={shown} className={className} />
        ) : (
          <span
            className={cn(
              // Hidden until the row is hovered: most songs without a chart never need a key.
              "inline-flex items-center justify-center rounded-full border border-dashed border-muted-foreground/50 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-hover/song:opacity-100",
              className,
            )}
          >
            ♯
          </span>
        )}
      </button>
      {anchor &&
        // On the body: the badge lives in narrow, clipped, draggable columns.
        createPortal(
          <div
            ref={panelRef}
            role="menu"
            style={{
              top: Math.min(anchor.bottom + 4, window.innerHeight - PANEL_HEIGHT - 8),
              left: Math.max(8, Math.min(anchor.left, window.innerWidth - PANEL_WIDTH - 8)),
              width: PANEL_WIDTH,
            }}
            className="fixed z-50 rounded-lg border border-border bg-background p-2 text-sm text-foreground shadow-xl"
          >
            <KeyChoices
              originalKey={originalKey}
              shown={shown}
              onChange={(key) => {
                setAnchor(null);
                onChange(key);
              }}
            />
          </div>,
          document.body,
        )}
    </>
  );
}

const PANEL_WIDTH = 240;
const PANEL_HEIGHT = 170;

function KeyChoices({
  originalKey,
  shown,
  onChange,
}: {
  originalKey: string | null;
  shown: string | null;
  onChange: (key: string | null) => void;
}) {
  const minor = originalKey ? isMinor(originalKey) : false;
  // The spellings bands use (Bb, not A#), and minor names for a minor song.
  const options = KEY_CHOICES.map((major, pc) => ({ pc, key: minor ? transposeKey("Cm", (pc + 9) % 12) : major }));
  const selectedPc = shown ? majorTonicPitchClass(shown) : null;
  const pick = (key: string | null) => {
    // A choice equal to the song's own key is no choice at all.
    onChange(key !== null && originalKey !== null && majorTonicPitchClass(key) === majorTonicPitchClass(originalKey) ? null : key);
  };
  return (
    <div role="group" aria-label="Key for this service">
      <div className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Key for this service
      </div>
      <div className="grid grid-cols-4 gap-1">
        {options.map(({ pc, key }) => {
          const selected = selectedPc === pc;
          return (
            <button
              key={pc}
              type="button"
              role="menuitemradio"
              aria-checked={selected}
              data-testid={`service-key-${key}`}
              onClick={() => pick(key)}
              className={cn(
                "flex items-center justify-center rounded-md py-1 transition-colors hover:bg-accent",
                selected && "bg-accent ring-1 ring-ring/50",
              )}
            >
              <KeyBadge songKey={key} className="size-7 text-[11px]" />
            </button>
          );
        })}
      </div>
      {originalKey && (
        <button
          type="button"
          role="menuitem"
          onClick={() => pick(null)}
          className="mt-1.5 w-full rounded-md border-t border-border px-2 py-1 text-center text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
        >
          Original key ({originalKey})
        </button>
      )}
    </div>
  );
}
