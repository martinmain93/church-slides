/**
 * The chord picker that opens over a clicked word in the song editor: the
 * key's palette (letters or Nashville numbers), a custom chord box, a
 * syllable selector for long words, and "add a passing chord".
 */

import { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";

import type { PlacedChord } from "@/lib/chords/chart";
import type { WordSyllable } from "@/lib/chords/chord";

import type { ChordDisplayMode } from "@/components/library/SongPreview";

export function ChordPickerPopover({
  popoverRef,
  inputRef,
  activeWord,
  lineChords,
  paletteItems,
  displayMode,
  customChord,
  setCustomChord,
  resolvedCustomPreview,
  onSelectSyllable,
  onApplyChord,
}: {
  popoverRef: React.RefObject<HTMLDivElement | null>;
  inputRef: React.RefObject<HTMLInputElement | null>;
  activeWord: {
    word: string;
    wordStart: number;
    wordEnd: number;
    chordPos: number;
    chord: string | null;
    syllables: WordSyllable[];
    isTrailing?: boolean;
  };
  lineChords: PlacedChord[];
  paletteItems: { chord: string; number: string; shortcutDigit?: string }[];
  displayMode: ChordDisplayMode;
  customChord: string;
  setCustomChord: (val: string) => void;
  resolvedCustomPreview: { resolved: string; nash: string } | null;
  onSelectSyllable: (syl: WordSyllable) => void;
  onApplyChord: (nextChord: string | null, opts?: { keepOpen?: boolean }) => void;
}) {
  const [appendPassingMode, setAppendPassingMode] = useState(false);

  useEffect(() => {
    setAppendPassingMode(false);
  }, [activeWord.chordPos, activeWord.wordStart]);

  const isMultiSyllable = activeWord.syllables.length > 1;
  const selectedSyllableIdx = isMultiSyllable
    ? activeWord.syllables.findIndex(
        (s) => activeWord.wordStart === s.start && activeWord.wordEnd === s.end,
      )
    : -1;

  function handlePaletteClick(chordSymbol: string) {
    if (appendPassingMode && activeWord.chord) {
      const combined = `${activeWord.chord} ${chordSymbol}`;
      setAppendPassingMode(false);
      onApplyChord(combined, { keepOpen: selectedSyllableIdx >= 0 });
      return;
    }

    if (selectedSyllableIdx >= 0) {
      onApplyChord(chordSymbol, { keepOpen: true });
      if (selectedSyllableIdx + 1 < activeWord.syllables.length) {
        onSelectSyllable(activeWord.syllables[selectedSyllableIdx + 1]!);
      }
      return;
    }

    onApplyChord(chordSymbol);
  }

  return (
    <div
      ref={popoverRef}
      data-testid="chord-picker-popover"
      className="absolute left-0 top-full z-40 mt-1 w-64 rounded-lg border border-border bg-popover p-2.5 text-popover-foreground shadow-xl"
    >
      <div className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-muted-foreground">
        <span>
          {activeWord.isTrailing ? (
            <>Trailing chord after line</>
          ) : (
            <>
              Chord on <strong className="text-foreground">&ldquo;{activeWord.word}&rdquo;</strong>
            </>
          )}
        </span>
        <div className="flex items-center gap-1">
          {activeWord.chord && (
            <button
              type="button"
              onClick={() => setAppendPassingMode((v) => !v)}
              data-testid="chord-picker-add-passing"
              title="Add a second / passing chord on this same syllable or word"
              className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors ${
                appendPassingMode
                  ? "bg-amber-500/25 text-amber-300 ring-1 ring-amber-400/60"
                  : "text-sky-400 hover:bg-sky-500/15"
              }`}
            >
              <Plus className="size-3" />
              Passing
            </button>
          )}
          {activeWord.chord && (
            <button
              type="button"
              onClick={() => onApplyChord(null, { keepOpen: isMultiSyllable })}
              data-testid="chord-picker-remove"
              className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px] text-destructive hover:bg-destructive/15"
            >
              <X className="size-3" />
              Remove
            </button>
          )}
        </div>
      </div>

      {/* Syllable Selector for multi-syllable words */}
      {isMultiSyllable && (
        <div
          data-testid="syllable-selector"
          className="mb-2 flex items-center gap-1 rounded border border-border/70 bg-muted/30 px-2 py-1"
        >
          <span className="mr-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            Syllable
          </span>
          <div className="flex flex-wrap items-center gap-0.5">
            {activeWord.syllables.map((syl, si) => {
              const isSelected = activeWord.chordPos >= syl.start && activeWord.chordPos < syl.end;
              const sylChords = lineChords.filter((c) => c.pos >= syl.start && c.pos < syl.end);
              const sylChordStr =
                sylChords.length > 0 ? sylChords.map((c) => c.chord).join(" ") : null;
              return (
                <button
                  key={si}
                  type="button"
                  data-testid={`syllable-chip-${si}`}
                  onClick={() => onSelectSyllable(syl)}
                  className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-xs transition-colors ${
                    isSelected
                      ? "bg-sky-500 text-white font-semibold"
                      : "bg-background/70 text-foreground hover:bg-accent"
                  }`}
                >
                  <span>{syl.text}</span>
                  {sylChordStr && (
                    <span
                      className={`text-[10px] ${
                        isSelected ? "text-sky-100" : "text-sky-400"
                      }`}
                    >
                      [{sylChordStr}]
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Diatonic Palette with both Chord Symbol and Nashville Number */}
      <div className="mb-2 grid grid-cols-3 gap-1">
        {paletteItems.map((item) => {
          const isCurrent = activeWord.chord === item.chord;
          const primaryLabel = displayMode === "numbers" ? item.number : item.chord;
          const secondaryLabel = displayMode === "numbers" ? item.chord : item.number;
          return (
            <button
              key={item.chord}
              type="button"
              onClick={() => handlePaletteClick(item.chord)}
              data-testid={`chord-quick-${item.chord}`}
              className={`flex items-center justify-between rounded border px-2 py-1 font-mono text-xs transition-colors ${
                isCurrent
                  ? "border-sky-400 bg-sky-500/25 font-bold text-sky-300"
                  : "border-border bg-muted/40 text-foreground hover:border-sky-500/40 hover:bg-sky-500/15 hover:text-sky-300"
              }`}
              title={`Press ${item.shortcutDigit ?? ""} or click to set ${item.chord} (${item.number})`}
            >
              <span className="font-semibold">{primaryLabel}</span>
              <span className="text-[10px] font-normal text-muted-foreground">
                {secondaryLabel}
              </span>
            </button>
          );
        })}
      </div>

      {/* Custom Chord or Nashville Number Entry (supports space-separated multi-chords like "G Gsus2") */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onApplyChord(customChord.trim() || null);
        }}
        className="flex flex-col gap-1"
      >
        <div className="flex items-center gap-1">
          <input
            ref={inputRef}
            type="text"
            value={customChord}
            onChange={(e) => setCustomChord(e.target.value)}
            placeholder="Chord(s) or # (e.g. G Gsus2, 1/3)"
            data-testid="chord-custom-input"
            className="h-7 min-w-0 flex-1 rounded border border-input bg-background px-2 font-mono text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            type="submit"
            className="h-7 rounded bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          >
            Set
          </button>
        </div>
        {resolvedCustomPreview &&
          resolvedCustomPreview.resolved.toLowerCase() !== customChord.trim().toLowerCase() && (
            <div
              data-testid="nashville-preview"
              className="flex items-center justify-between px-1 text-[10px] text-sky-400"
            >
              <span>
                Number <strong className="font-mono">{customChord.trim()}</strong> &rarr;{" "}
                <strong className="font-mono">{resolvedCustomPreview.resolved}</strong>
              </span>
              <span className="font-mono text-muted-foreground">
                ({resolvedCustomPreview.nash})
              </span>
            </div>
          )}
      </form>
    </div>
  );
}
