/**
 * The "how the lyrics look" choices — slides or scrolling, colour scheme,
 * typeface, size and weight: as a modal (operator top bar, setlist header,
 * opened by the icon button here) and inline on the Display settings page
 * (`DisplayLookControls`).
 *
 * Every choice saves as it's made (`saveDisplayLook`), so the stages in this
 * window and the audience window follow live, and the choice sticks on this
 * device. Previews render the real `SlideStage` at projector size, scaled
 * down, so what you pick here is what the room gets.
 *
 * With `override`, the same modal edits a style that overrides the default —
 * scripture's, or one song's or passage's in a setlist — and offers "Use
 * default style" to drop it. The lyrics mode (slides or scrolling) belongs to
 * the default only.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { SlideStage } from "@/components/presentation/SlideStage";
import {
  DEFAULT_LOOK,
  DisplayLookContext,
  FONT_OPTIONS,
  SIZE_OPTIONS,
  THEME_OPTIONS,
  WEIGHT_OPTIONS,
  faceStyle,
  saveDisplayLook,
  useDisplayLook,
  type DisplayLook,
} from "@/lib/displayLook";
import { saveDisplayPref, useDisplayPref } from "@/lib/displayPrefs";
import type { ParsedSlide } from "@/lib/parseSong";
import type { DisplayPref } from "@/lib/presentation/flow";
import { cn } from "@/lib/utils";

function sample(lyrics: string): ParsedSlide {
  return {
    id: "display-look-sample",
    section: { type: "verse", number: 1 },
    order_in_song: 0,
    order_in_section: 0,
    lyrics,
    words: lyrics.split(/\s+/),
  };
}

const SAMPLE = sample(
  "Amazing grace, how sweet the sound\nThat saved a wretch like me\nI once was lost, but now am found\nWas blind, but now I see",
);
const SWATCH = sample("Amazing grace");

const DISPLAYS: { value: DisplayPref; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Automatic",
    hint: "Scrolls with the singing while the AI follows; slides otherwise.",
  },
  { value: "slide", label: "Slides", hint: "One slide at a time." },
  {
    value: "continuous",
    label: "Scrolling",
    hint: "The whole song as one column, kept on the words being sung.",
  },
];

/** A style that overrides the default for some of what's on screen. */
export interface LookOverride {
  title: string;
  description: string;
  value: DisplayLook;
  onChange: (look: DisplayLook) => void;
  /** Drop the override. Absent while there's nothing to drop. */
  onUseDefault?: () => void;
}

export function DisplayLookDialog({
  onClose,
  override,
}: {
  onClose: () => void;
  override?: LookOverride;
}) {
  // Focus Done for Enter/Escape, but don't let that scroll the modal to its
  // bottom — the lyrics choice at the top is what most people came for.
  const doneRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => doneRef.current?.focus({ preventScroll: true }), []);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      // Don't let the presentation keymap also see it (it would exit).
      ev.preventDefault();
      ev.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="display-look-title"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-full w-full max-w-xl flex-col overflow-y-auto rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <h2 id="display-look-title" className="text-base font-semibold">
          {override?.title ?? "Display look"}
        </h2>
        <p className="mb-4 text-xs text-muted-foreground">
          {override?.description ??
            "Applies to all services on this computer — slides and scrolling lyrics alike."}
        </p>
        <DisplayLookControls
          override={override}
          footer={
            <Button ref={doneRef} type="button" size="sm" onClick={onClose}>
              Done
            </Button>
          }
        />
      </div>
    </div>
  );
}

/**
 * The choices themselves — lyrics mode, colours, typeface, size and weight,
 * with a live preview. Shown in the dialog (operator, setlist) and inline on
 * the Display settings page.
 */
export function DisplayLookControls({
  footer,
  override,
}: {
  footer?: ReactNode;
  override?: Pick<LookOverride, "value" | "onChange" | "onUseDefault">;
}) {
  const defaultLook = useDisplayLook();
  const look = override?.value ?? defaultLook;
  const displayPref = useDisplayPref();
  const set = (patch: Partial<DisplayLook>) =>
    override ? override.onChange({ ...look, ...patch }) : saveDisplayLook({ ...look, ...patch });

  return (
    <>
      {!override && (
        <Section label="Lyrics">
          <div role="radiogroup" aria-label="Lyrics" className="grid grid-cols-1 gap-1 sm:grid-cols-3">
            {DISPLAYS.map((o) => (
              <Choice
                key={o.value}
                checked={displayPref === o.value}
                onSelect={() => saveDisplayPref(o.value)}
              >
                <span className="text-sm font-medium">{o.label}</span>
                <span className="block text-[11px] text-muted-foreground">{o.hint}</span>
              </Choice>
            ))}
          </div>
        </Section>
      )}

      {/* Capped: inline on the settings page the card is wider than the
          dialog, and a full-width preview pushed every choice off screen. */}
      <ScaledPreview look={look} slide={SAMPLE} className="mx-auto mb-5 max-w-md" />

      <Section label="Colours">
        <div role="radiogroup" aria-label="Colours" className="grid grid-cols-3 gap-2">
          {THEME_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={look.theme === o.value}
              title={o.hint}
              onClick={() => set({ theme: o.value })}
              className={cn(
                "rounded-md border p-1.5 text-left text-xs transition-colors",
                look.theme === o.value
                  ? "border-primary ring-1 ring-primary"
                  : "border-border hover:border-foreground/40",
              )}
            >
              <ScaledPreview look={{ ...look, theme: o.value }} slide={SWATCH} />
              <div className="mt-1.5 px-0.5 font-medium">{o.label}</div>
            </button>
          ))}
        </div>
      </Section>

      <Section label="Typeface">
        <div role="radiogroup" aria-label="Typeface" className="grid grid-cols-1 gap-1 sm:grid-cols-2">
          {FONT_OPTIONS.map((o) => (
            <Choice key={o.value} checked={look.font === o.value} onSelect={() => set({ font: o.value })}>
              <span className="text-sm" style={faceStyle({ ...look, font: o.value })}>
                {o.label}
              </span>
              <span className="block text-[11px] text-muted-foreground">{o.hint}</span>
            </Choice>
          ))}
        </div>
      </Section>

      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[3fr_2fr]">
        <Section label="Size">
          <Segmented label="Size" options={SIZE_OPTIONS} value={look.size} onChange={(size) => set({ size })} />
        </Section>
        <Section label="Weight">
          <Segmented
            label="Weight"
            options={WEIGHT_OPTIONS}
            value={look.weight}
            onChange={(weight) => set({ weight })}
          />
        </Section>
      </div>

      <div className="mt-2 flex items-center justify-between">
        {override ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={override.onUseDefault}
            disabled={!override.onUseDefault}
          >
            Use default style
          </Button>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => saveDisplayLook(DEFAULT_LOOK)}
            disabled={(Object.keys(DEFAULT_LOOK) as (keyof DisplayLook)[]).every(
              (k) => look[k] === DEFAULT_LOOK[k],
            )}
          >
            Reset to default
          </Button>
        )}
        {footer}
      </div>
    </>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="mb-4">
      <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </h3>
      {children}
    </section>
  );
}

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-md border border-border p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-8 flex-1 whitespace-nowrap rounded px-1 transition-colors",
            value === o.value
              ? "bg-accent font-medium text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Choice({
  checked,
  onSelect,
  children,
}: {
  checked: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onSelect}
      className={cn(
        "rounded-md border px-3 py-2 text-left transition-colors",
        checked ? "border-primary bg-accent/40 ring-1 ring-primary" : "border-border hover:border-foreground/40",
      )}
    >
      {children}
    </button>
  );
}

/** Nominal projector size the previews are scaled down from. */
const STAGE_W = 1280;
const STAGE_H = 720;

/**
 * `SlideStage` rendered at projector size and transform-scaled to fit, so
 * type, bevels and fringes keep their real proportions in a thumbnail.
 */
function ScaledPreview({
  look,
  slide,
  className,
}: {
  look: DisplayLook;
  slide: ParsedSlide;
  className?: string;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const apply = () => setScale(el.clientWidth / STAGE_W);
    apply();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={boxRef}
      // `shrink-0`: in the dialog's scrolling flex column an overflow-hidden
      // 16:9 box may otherwise shrink to nothing when the window is short.
      className={cn("relative aspect-video w-full shrink-0 overflow-hidden rounded bg-black", className)}
    >
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{
          width: STAGE_W,
          height: STAGE_H,
          transform: `scale(${scale})`,
          visibility: scale > 0 ? "visible" : "hidden",
        }}
      >
        <DisplayLookContext.Provider value={look}>
          <SlideStage slide={slide} blankMode="none" />
        </DisplayLookContext.Provider>
      </div>
    </div>
  );
}
