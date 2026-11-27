/**
 * One 16:9 slide card in the song editor's projector grid, and the glow an
 * AI take leaves on the cards it changed.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Copy, GripVertical, MoreHorizontal, Pencil, Scissors, Sparkles, Trash2 } from "lucide-react";

import { sectionLabel, sectionLabelClass, type ParsedSection, type SectionType } from "@/lib/parseSong";
import type { SongSuggestion } from "@/lib/chords/suggestions";
import { stageThumbnailStyle } from "@/components/presentation/StageSurface";
import { type DisplayLook } from "@/lib/look";
import { Textarea, growToFit } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { canSplit, isCrowded, offerSplit } from "@/lib/slideSplit";
import { MiniSlide, useMiniStage } from "@/components/presentation/MiniSlide";

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
  const [menu, setMenu] = useState<"section" | "more" | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // A menu closes on Escape or a tap anywhere outside the card and its menus.
  useEffect(() => {
    if (!menu) return;
    function onDown(e: PointerEvent) {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) setMenu(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenu(null);
    }
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const dndTransform = CSS.Transform.toString(transform);
  const style: React.CSSProperties = {
    ...(dndTransform ? { transform: dndTransform } : {}),
    ...(transition ? { transition } : {}),
    opacity: isDragging ? 0.6 : 1,
  };
  const setRefs = (el: HTMLDivElement | null) => {
    setNodeRef(el);
    cardRef.current = el;
  };

  const lineCount = Math.max(1, editingDraft.split("\n").length);
  const mini = useMiniStage(look);
  const [overflows, setOverflows] = useState(false);
  // The open card grows to show every word; the others stay a 16:9 picture.
  // A true-scale 16:9 picture of the screen; only the box being typed in
  // grows, so nothing typed is ever hidden.
  const expanded = editing;
  // Clear of the corner buttons, which are bigger on a touch screen.
  const contentPos = expanded
    ? "relative col-start-1 row-start-1 py-9 [@media(pointer:coarse)]:pt-14"
    : "absolute inset-0 py-5";

  // Fit the box to its words again once the card knows its font size (it's
  // measured after the first paint), so long lines wrap into view rather
  // than behind a scrollbar.
  const box = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    if (editing) growToFit(box.current);
  }, [editing, editingDraft, mini.text.fontSize]);

  // The corner buttons show on the selected card (and on hover, with a
  // mouse). Hidden, they take no taps: one stray tap mustn't delete a slide.
  const showActions = selected || editing;
  const draft = editing ? editingDraft : slide.lyrics;

  return (
    <div
      ref={setRefs}
      style={style}
      data-testid="preview-slide"
      data-selected={selected ? "true" : undefined}
      data-expanded={expanded ? "true" : undefined}
      className="group relative w-full"
    >
    <div
      style={stageThumbnailStyle(look)}
      className={`relative w-full overflow-hidden rounded-md border shadow-xs transition-[border-color,box-shadow] duration-250 ease-out ${
        // One column, the card's width: never as wide as the words in it.
        expanded ? "grid grid-cols-1" : "aspect-video"
      } ${
        selected || editing
          ? "border-primary ring-2 ring-primary"
          : "border-border dark:border-white/25 [@media(hover:hover)]:hover:border-primary/50 [@media(hover:hover)]:hover:ring-1 [@media(hover:hover)]:hover:ring-primary/50"
      }`}
    >
      {expanded && <div aria-hidden className="col-start-1 row-start-1 aspect-video w-full" />}
      {/* Keeps the labels and buttons readable over a slide full of words. */}
      {editable && (
        <div
          aria-hidden
          className={`pointer-events-none absolute inset-x-0 top-0 z-20 h-12 bg-gradient-to-b from-black/60 to-transparent ${REVEAL} ${showActions ? SHOWN : ""}`}
        />
      )}
      {/* Top-left section badge (tappable when editable, to change the section) */}
      <div className="absolute left-2 top-2 z-30">
        {editable ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setMenu((m) => (m === "section" ? null : "section"));
            }}
            title="Change section"
            aria-label={`Section: ${sectionLabel(slide.section)}. Change section`}
            aria-expanded={menu === "section"}
            className={`truncate rounded bg-black/65 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors hover:bg-black/85 [@media(pointer:coarse)]:px-2.5 [@media(pointer:coarse)]:py-1.5 [@media(pointer:coarse)]:text-[11px] ${sectionLabelClass(slide.section.type)}`}
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
      </div>

      {/* Slide actions, in the same order as a service's slide cards: Edit,
          Split, then the rest behind "⋯", and the drag handle. */}
      {editable && (
        <div className={`absolute right-2 top-2 z-30 flex items-center gap-1 ${REVEAL} ${showActions ? SHOWN : ""}`}>
          {!editing && (
            <CardAction label="Edit slide text" onClick={onStartEdit}>
              <Pencil className="size-3.5 shrink-0" />
            </CardAction>
          )}
          {onSplit &&
            (offerSplit(draft, { midLine: splitMidLine }) ||
              (overflows && canSplit(slide.lyrics, { midLine: splitMidLine }))) && (
            <CardAction
              label="Split slide in two"
              onClick={onSplit}
              // A crowded slide asks for it.
              tone={overflows || isCrowded(draft) ? "suggest" : "plain"}
              // Clicking it while typing: the text box's blur mustn't eat the click.
              onMouseDown={(e) => e.preventDefault()}
            >
              <Scissors className="size-3.5 shrink-0" />
            </CardAction>
          )}
          <CardAction
            label="More slide actions"
            onClick={() => setMenu((m) => (m === "more" ? null : "more"))}
            onMouseDown={(e) => e.preventDefault()}
          >
            <MoreHorizontal className="size-3.5 shrink-0" />
          </CardAction>
          <button
            {...attributes}
            {...listeners}
            onClick={(e) => e.stopPropagation()}
            aria-label="Drag to reorder slide"
            title="Drag to reorder slide"
            // touch-none: on a phone, dragging the handle moves the slide, not the page.
            className={cn(CARD_ACTION, "cursor-grab touch-none [@media(hover:hover)]:hover:border-white/40 [@media(hover:hover)]:hover:bg-zinc-700 active:cursor-grabbing")}
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
          <div ref={mini.ref} style={mini.container} className="w-full">
          <Textarea
            autoFocus
            ref={box}
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
            className="min-h-0 w-full resize-none overflow-y-auto rounded-sm font-bold leading-tight border-none bg-transparent p-0 text-center leading-snug shadow-none outline-none ring-offset-0 focus-visible:ring-0 focus-visible:ring-offset-0"
            style={{ ...mini.text, maxWidth: undefined }}
          />
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={(e) => {
            // The first tap selects (showing its buttons); a tap on the
            // selected card, or a double-click, opens its words.
            if (editable && selected) {
              onStartEdit();
            } else {
              e.currentTarget.blur();
              onSelect();
            }
          }}
          onDoubleClick={editable ? onStartEdit : undefined}
          aria-label={editable && selected ? "Edit slide text" : "Select slide"}
          className="absolute inset-0 cursor-pointer overflow-hidden text-center"
        >
          <MiniSlide lyrics={slide.lyrics} look={look} empty="Empty slide" onOverflow={setOverflows} />
        </button>
      )}

      {/* While typing, the words are measured on a hidden true-scale screen,
          so "Too long" stays honest as they grow. */}
      {editing && (
        <>
          <div aria-hidden className="invisible absolute inset-x-0 top-0 aspect-video">
            <MiniSlide lyrics={editingDraft} look={look} empty="" onOverflow={setOverflows} />
          </div>
          {overflows && (
            <span className="pointer-events-none absolute bottom-1.5 left-1/2 z-30 -translate-x-1/2 rounded bg-amber-500/90 px-1.5 py-0.5 font-sans text-[10px] font-semibold text-zinc-950">
              Too long for the screen
            </span>
          )}
        </>
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

      {/* The menus sit outside the clipped card, so none of them is cut off. */}
      {menu === "section" && (
        <div
          role="menu"
          onClick={(e) => e.stopPropagation()}
          className="absolute left-2 top-9 z-50 flex w-max min-w-48 flex-col gap-0.5 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-xl"
        >
          {SECTION_CHOICES.map((choice) => (
            <div key={choice.type} className="flex items-center justify-between gap-1">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onChangeSection({ type: choice.type, number: 1 });
                  setMenu(null);
                }}
                className="min-h-8 flex-1 rounded px-2 text-left text-xs hover:bg-accent [@media(pointer:coarse)]:min-h-10 [@media(pointer:coarse)]:text-sm"
              >
                {choice.label}
              </button>
              {NUMBERED.has(choice.type) && (
                <div className="flex items-center">
                  {[1, 2, 3, 4].map((num) => (
                    <button
                      key={num}
                      type="button"
                      role="menuitem"
                      aria-label={`${choice.label} ${num}`}
                      onClick={() => {
                        onChangeSection({ type: choice.type, number: num });
                        setMenu(null);
                      }}
                      className="flex size-7 items-center justify-center rounded font-mono text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground [@media(pointer:coarse)]:size-9 [@media(pointer:coarse)]:text-xs"
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
      {menu === "more" && (
        <div
          role="menu"
          onClick={(e) => e.stopPropagation()}
          className="absolute right-2 top-10 z-50 min-w-36 rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-xl [@media(pointer:coarse)]:top-14"
        >
          <MenuItem
            label="Duplicate"
            icon={<Copy className="size-3.5" />}
            onClick={() => {
              setMenu(null);
              onDuplicate();
            }}
          />
          <MenuItem
            label="Delete slide"
            destructive
            icon={<Trash2 className="size-3.5" />}
            onClick={() => {
              setMenu(null);
              onDelete();
            }}
          />
        </div>
      )}
    </div>
  );
}

const NUMBERED = new Set<SectionType>(["verse", "chorus", "bridge"]);

/** Hidden corner chrome: see-through and untappable until it's shown. */
const REVEAL =
  "invisible opacity-0 transition-[opacity,visibility] duration-200 ease-out group-focus-within:visible group-focus-within:opacity-100 [@media(hover:hover)]:group-hover:visible [@media(hover:hover)]:group-hover:opacity-100";
const SHOWN = "!visible !opacity-100";

function MenuItem({
  label,
  icon,
  onClick,
  destructive,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex min-h-8 w-full items-center gap-2 rounded-sm px-2 text-left hover:bg-accent [@media(pointer:coarse)]:min-h-11 ${
        destructive ? "text-destructive" : ""
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

const CARD_ACTION =
  "flex size-7 [@media(pointer:coarse)]:size-10 items-center justify-center rounded-md border border-white/15 bg-zinc-900/90 text-zinc-100 shadow-sm outline-none transition-colors duration-150 focus-visible:ring-1 focus-visible:ring-white/40";

// Hover only where there is one: on a phone a tapped button would stay lit.
const TONE = {
  plain: "[@media(hover:hover)]:hover:border-white/40 [@media(hover:hover)]:hover:bg-zinc-700",
  suggest:
    "border-amber-400/70 text-amber-300 [@media(hover:hover)]:hover:border-amber-400 [@media(hover:hover)]:hover:bg-amber-500 [@media(hover:hover)]:hover:text-zinc-950",
};

/** One of a slide card's corner buttons. Neutral, except a split the slide
 * is asking for (amber). */
function CardAction({
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
      className={cn(CARD_ACTION, TONE[tone])}
    >
      {children}
    </button>
  );
}
