/**
 * One 16:9 slide card in the song editor's projector grid, and the glow an
 * AI take leaves on the cards it changed.
 */

import { useEffect, useRef, useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Copy, GripVertical, Pencil, Scissors, Sparkles, Trash2 } from "lucide-react";

import { sectionLabel, sectionLabelClass, type ParsedSection, type SectionType } from "@/lib/parseSong";
import type { SongSuggestion } from "@/lib/chords/suggestions";
import { inkStyle, stageThumbnailStyle } from "@/components/presentation/StageSurface";
import { SIZE_SCALE, type DisplayLook } from "@/lib/displayLook";
import { Textarea, growToFit } from "@/components/ui/textarea";
import { isCrowded, offerSplit } from "@/lib/slideSplit";

const SECTION_CHOICES: { type: SectionType; label: string }[] = [
  { type: "verse", label: "Verse" },
  { type: "chorus", label: "Chorus" },
  { type: "pre-chorus", label: "Pre-Chorus" },
  { type: "bridge", label: "Bridge" },
  { type: "tag", label: "Tag" },
  { type: "intro", label: "Intro" },
  { type: "outro", label: "Outro" },
];

export function AiChangeGlow() {
  return (
    <span className="ai-change-glow pointer-events-none absolute inset-0 z-20 rounded-[inherit]" aria-hidden>
      <Sparkles className="ai-spark absolute right-2 top-2 size-4 text-amber-200" />
      <Sparkles className="ai-spark ai-spark-late absolute left-3 top-3 size-3 text-amber-100" />
      <Sparkles className="ai-spark ai-spark-end absolute bottom-2.5 right-8 size-3.5 text-amber-300" />
    </span>
  );
}

export function ProjectorSlideCard({
  slide,
  look,
  editable,
  selected,
  editing,
  editingDraft,
  slideSuggestions = [],
  activeSuggestionId = null,
  onAcceptSuggestion,
  onSelect,
  onStartEdit,
  onChangeDraft,
  onStopEdit,
  onChangeSection,
  onDuplicate,
  onSplit,
  splitMidLine = true,
  onDelete,
}: {
  slide: { id: string; section: ParsedSection; lyrics: string };
  look: DisplayLook;
  editable: boolean;
  selected: boolean;
  editing: boolean;
  editingDraft: string;
  slideSuggestions?: SongSuggestion[];
  activeSuggestionId?: string | null;
  onAcceptSuggestion?: (suggestion: SongSuggestion) => void;
  onSelect: () => void;
  onStartEdit: () => void;
  onChangeDraft: (next: string) => void;
  onStopEdit: () => void;
  onChangeSection: (next: ParsedSection) => void;
  onDuplicate: () => void;
  /** Cut the slide in two near its middle (see `smartSplit`). */
  onSplit?: () => void;
  /** Whether a split may cut one long line (not in a song with chords). */
  splitMidLine?: boolean;
  onDelete: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: slide.id,
    disabled: !editable,
  });
  const [sectionMenuOpen, setSectionMenuOpen] = useState(false);
  const badgeRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!sectionMenuOpen) return;
    function onDown(e: MouseEvent) {
      if (badgeRef.current && !badgeRef.current.contains(e.target as Node)) {
        setSectionMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [sectionMenuOpen]);

  const dndTransform = CSS.Transform.toString(transform);
  const style: React.CSSProperties = {
    ...stageThumbnailStyle(look),
    ...(dndTransform ? { transform: dndTransform } : {}),
    ...(transition ? { transition } : {}),
    opacity: isDragging ? 0.6 : 1,
  };

  const fontSizePx = Math.round(15 * SIZE_SCALE[look.size]);
  const lineCount = Math.max(1, editingDraft.split("\n").length);
  // The open card grows to show every word; the others stay a 16:9 picture.
  const expanded = selected || editing;
  const contentPos = expanded ? "relative col-start-1 row-start-1 py-9" : "absolute inset-0 py-5";

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid="preview-slide"
      data-selected={selected ? "true" : undefined}
      data-expanded={expanded ? "true" : undefined}
      className={`group relative w-full overflow-hidden rounded-md border shadow-xs transition-[border-color,box-shadow] duration-250 ease-out ${
        expanded ? "grid" : "aspect-video"
      } ${
        selected || editing
          ? "border-primary ring-2 ring-primary"
          : "border-border hover:border-primary/50 hover:ring-1 hover:ring-primary/50"
      }`}
    >
      {expanded && <div aria-hidden className="col-start-1 row-start-1 aspect-video w-full" />}
      {/* Top-left section badge (clickable when editable to change section type/number) */}
      <div ref={badgeRef} className="absolute left-2.5 top-2 z-30">
        {editable ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setSectionMenuOpen((v) => !v);
            }}
            title="Click to change section label"
            className={`truncate rounded bg-black/65 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors hover:bg-black/85 ${sectionLabelClass(slide.section.type)}`}
          >
            {sectionLabel(slide.section)}
          </button>
        ) : (
          <span
            className={`pointer-events-none truncate rounded bg-black/65 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${sectionLabelClass(slide.section.type)}`}
          >
            {sectionLabel(slide.section)}
          </span>
        )}
        {sectionMenuOpen && (
          <div
            onClick={(e) => e.stopPropagation()}
            className="mt-1 flex w-36 flex-col gap-0.5 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-xl"
          >
            {SECTION_CHOICES.map((choice) => (
              <div key={choice.type} className="flex items-center justify-between gap-1">
                <button
                  type="button"
                  onClick={() => {
                    onChangeSection({ type: choice.type, number: 1 });
                    setSectionMenuOpen(false);
                  }}
                  className="flex-1 rounded px-2 py-1 text-left text-[11px] hover:bg-accent"
                >
                  {choice.label}
                </button>
                {(choice.type === "verse" || choice.type === "chorus" || choice.type === "bridge") && (
                  <div className="flex items-center gap-0.5 pr-1">
                    {[1, 2, 3, 4].map((num) => (
                      <button
                        key={num}
                        type="button"
                        onClick={() => {
                          onChangeSection({ type: choice.type, number: num });
                          setSectionMenuOpen(false);
                        }}
                        className="rounded px-1 py-0.5 font-mono text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        {num}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Slide actions, in the same order as a service's slide cards: Edit,
          Split, then the rest. Shown on hover, and always on the open card
          (a touch screen has no hover). */}
      {editable && (
        <div
          className={`absolute right-2 top-2 z-30 flex items-center gap-1 transition-opacity duration-200 ease-out group-hover:opacity-100 focus-within:opacity-100 ${
            selected || editing ? "opacity-100" : "opacity-0"
          }`}
        >
          <CardAction label="Edit slide text" onClick={onStartEdit}>
            <Pencil className="size-3.5 shrink-0" />
          </CardAction>
          {onSplit && offerSplit(editing ? editingDraft : slide.lyrics, { midLine: splitMidLine }) && (
            <CardAction
              label="Split slide in two"
              onClick={onSplit}
              // A crowded slide asks for it.
              tone={isCrowded(editing ? editingDraft : slide.lyrics) ? "suggest" : "plain"}
              // Clicking it while typing: the text box's blur mustn't eat the click.
              onMouseDown={(e) => e.preventDefault()}
            >
              <Scissors className="size-3.5 shrink-0" />
            </CardAction>
          )}
          <CardAction label="Duplicate slide" onClick={onDuplicate}>
            <Copy className="size-3.5 shrink-0" />
          </CardAction>
          <CardAction label="Delete slide" onClick={onDelete} tone="danger">
            <Trash2 className="size-3.5 shrink-0" />
          </CardAction>
          <button
            {...attributes}
            {...listeners}
            onClick={(e) => e.stopPropagation()}
            aria-label="Drag to reorder slide"
            title="Drag to reorder slide"
            className={`${CARD_ACTION} cursor-grab hover:border-white/40 hover:bg-zinc-700 active:cursor-grabbing`}
          >
            <GripVertical className="size-3.5 shrink-0" />
          </button>
        </div>
      )}

      {editing ? (
        <div
          onClick={() => onSelect()}
          className={`${contentPos} flex items-center justify-center overflow-hidden px-4 text-center`}
        >
          <Textarea
            autoFocus
            ref={growToFit}
            rows={lineCount}
            value={editingDraft}
            placeholder="New slide lyrics…"
            onChange={(e) => {
              onChangeDraft(e.target.value);
              growToFit(e.target);
            }}
            onBlur={onStopEdit}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                e.currentTarget.blur();
                onStopEdit();
              }
            }}
            className="min-h-0 w-full resize-none overflow-y-auto rounded-sm border-none bg-transparent p-0 text-center leading-snug shadow-none outline-none ring-offset-0 focus-visible:ring-0 focus-visible:ring-offset-0"
            style={{
              ...inkStyle(look),
              fontSize: `${fontSizePx}px`,
            }}
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={(e) => {
            if (editable) {
              onStartEdit();
            } else {
              e.currentTarget.blur();
              onSelect();
            }
          }}
          aria-label="Select slide"
          className={`${contentPos} flex cursor-pointer items-center justify-center overflow-hidden px-4 text-center`}
        >
          <div
            className={`${expanded ? "" : "max-h-full "}overflow-hidden whitespace-pre-wrap text-center leading-snug`}
            style={{
              ...inkStyle(look),
              fontSize: `${fontSizePx}px`,
            }}
          >
            {slide.lyrics ? (
              slide.lyrics
            ) : (
              <span className="font-sans text-xs font-normal italic opacity-50">
                Empty slide — click to edit
              </span>
            )}
          </div>
        </button>
      )}

      {/* Bottom Ghost Suggestion Pill on 16:9 Slide Card */}
      {!editing && slideSuggestions.length > 0 && onAcceptSuggestion && (
        <div className="absolute inset-x-2 bottom-2 z-30 flex items-center justify-center">
          {(() => {
            const primary =
              slideSuggestions.find((s) => s.id === activeSuggestionId) ??
              slideSuggestions[0]!;
            const isPrimaryActive = primary.id === activeSuggestionId;
            return (
              <button
                type="button"
                data-testid="projector-ghost-suggestion-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onAcceptSuggestion(primary);
                }}
                title={`${primary.reason} — Click or press Tab to accept`}
                className={`inline-flex max-w-full items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-[11px] font-medium shadow-md backdrop-blur-xs transition-all ${
                  isPrimaryActive
                    ? "border-amber-400 bg-amber-950/90 text-amber-200 ring-1 ring-amber-400/60"
                    : "border-amber-400/50 bg-zinc-900/90 text-amber-300 hover:border-amber-400 hover:bg-amber-950/90"
                }`}
              >
                <Sparkles className="size-3 shrink-0 text-amber-400" />
                <span className="truncate">{primary.label}</span>
                {isPrimaryActive && (
                  <kbd className="shrink-0 rounded border border-amber-400/40 bg-amber-900/80 px-1 font-mono text-[9px] text-amber-100">
                    Tab
                  </kbd>
                )}
              </button>
            );
          })()}
        </div>
      )}
    </div>
  );
}

const CARD_ACTION =
  "flex size-7 items-center justify-center rounded-md border border-white/15 bg-zinc-900/90 text-zinc-100 shadow-sm outline-none transition-colors duration-150 focus-visible:ring-1 focus-visible:ring-white/40";

const TONE = {
  plain: "hover:border-white/40 hover:bg-zinc-700",
  suggest: "border-amber-400/70 text-amber-300 hover:border-amber-400 hover:bg-amber-500 hover:text-zinc-950",
  danger: "hover:border-destructive hover:bg-destructive hover:text-destructive-foreground",
};

/** One of a slide card's corner buttons. Neutral, except Delete (red) and a
 * split the slide is asking for (amber). */
export function CardAction({
  label,
  onClick,
  onMouseDown,
  tone = "plain",
  children,
}: {
  label: string;
  onClick: () => void;
  onMouseDown?: (e: React.MouseEvent) => void;
  tone?: keyof typeof TONE;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseDown={onMouseDown}
      aria-label={label}
      title={label}
      className={`${CARD_ACTION} ${TONE[tone]}`}
    >
      {children}
    </button>
  );
}
