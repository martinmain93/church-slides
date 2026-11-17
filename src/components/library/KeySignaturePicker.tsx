/**
 * The song editor's key picker: twelve keys, major or relative minor, each
 * in its key colour, with a sharps/flats toggle.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { findKeySignatureOption, keySignatureOptions } from "@/lib/chords/chord";
import { KeyBadge } from "@/components/KeyBadge";

export function KeySignaturePicker({
  value,
  preferFlats,
  hasChords,
  onChange,
}: {
  value: string;
  preferFlats: boolean;
  hasChords: boolean;
  onChange: (nextKey: string, shouldTranspose: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [transposeMode, setTransposeMode] = useState(true);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const options = useMemo(() => keySignatureOptions(preferFlats), [preferFlats]);
  const selected = useMemo(
    () => findKeySignatureOption(value, preferFlats),
    [value, preferFlats],
  );

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative inline-flex items-center gap-1.5">
      <span className="text-muted-foreground">Key</span>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        data-testid="chart-key"
        title="The key signature for this song (click to change or transpose)"
        className="inline-flex h-7 items-center gap-1.5 rounded border border-input bg-background px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {selected ? (
          <>
            <KeyBadge songKey={selected.major} className="size-5 text-[10px]" />
            <span className="text-muted-foreground">({selected.minor})</span>
          </>
        ) : (
          <span className="px-1 text-muted-foreground">Select key</span>
        )}
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </button>

      {open && (
        <div
          role="listbox"
          data-testid="chart-key-menu"
          className="absolute left-0 top-full z-50 mt-1.5 w-64 rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-xl"
        >
          {hasChords && selected && (
            <div className="mb-2 rounded-md bg-muted/50 p-1">
              <div className="mb-1 px-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                When changing key
              </div>
              <div className="grid grid-cols-2 gap-1 text-[11px]">
                <button
                  type="button"
                  data-testid="key-mode-transpose"
                  onClick={() => setTransposeMode(true)}
                  className={`rounded px-2 py-1 font-medium transition-colors ${
                    transposeMode
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Transpose chords
                </button>
                <button
                  type="button"
                  data-testid="key-mode-relabel"
                  onClick={() => setTransposeMode(false)}
                  className={`rounded px-2 py-1 font-medium transition-colors ${
                    !transposeMode
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Relabel only
                </button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-1">
            {options.map((opt) => {
              const isSelected = selected?.pc === opt.pc;
              return (
                <button
                  key={opt.pc}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  data-testid={`key-option-${opt.major}`}
                  onClick={() => {
                    onChange(opt.major, transposeMode);
                    setOpen(false);
                  }}
                  className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors ${
                    isSelected
                      ? "bg-accent font-semibold text-accent-foreground ring-1 ring-ring/50"
                      : "hover:bg-accent/50"
                  }`}
                >
                  <KeyBadge songKey={opt.major} className="size-6 text-[11px]" />
                  <span className="text-muted-foreground">({opt.minor})</span>
                </button>
              );
            })}
          </div>
          {selected && (
            <button
              type="button"
              onClick={() => {
                onChange("", false);
                setOpen(false);
              }}
              className="mt-1.5 w-full rounded-md border-t border-border px-2 py-1 text-center text-[11px] text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
            >
              Clear key
            </button>
          )}
        </div>
      )}
    </div>
  );
}
