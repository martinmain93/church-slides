import { useMemo, useState } from "react";
import { Pencil } from "lucide-react";

import type { SongChart } from "@/core/library";
import { ChordLyrics } from "@/components/band/ChordLyrics";
import { KeyBadge } from "@/components/KeyBadge";
import { alignSlides, chartHasChords, parseStoredChart, transposeLines, type ChordChart } from "@/lib/chords/chart";
import { semitonesToKey } from "@/lib/chords/chord";
import { sectionLabel } from "@/lib/parseSong";
import { resolveSlideSequence } from "@/lib/slideSequence";
import { href } from "@/web/route";
import { Empty, ErrorLine, PageHeader, iconButtonClass, useLibraryData, useTitle } from "@/web/ui";
import { cn } from "@/lib/utils";

/** The key a song's chart is written in: the key stored with it, or the
 * chart's own. One rule for every page. */
export function writtenKeyOf(stored: SongChart | null | undefined): string | null {
  if (!stored) return null;
  return stored.original_key ?? parseStoredChart(stored.chart_json)?.key ?? null;
}

/** The words of a song in the order they're sung (its Order, else each
 * section once), with the chords over them when it has a chart. `playKey`
 * transposes them (a service's key for the song). */
export function SongWords({
  body,
  order,
  chart,
  writtenKey,
  playKey,
  showChords,
}: {
  body: string;
  order: string | null;
  chart: ChordChart | null;
  writtenKey: string | null;
  playKey: string | null;
  showChords: boolean;
}) {
  const sections = useMemo(() => {
    const slides = resolveSlideSequence(body, order);
    const aligned = chart && showChords ? alignSlides(chart, slides) : null;
    const shift = writtenKey && playKey ? semitonesToKey(writtenKey, playKey) : 0;
    const out: { label: string; stanzas: { text: string; chords: { pos: number; chord: string }[] }[][] }[] = [];
    let lastSection: string | null = null;
    slides.forEach((slide, i) => {
      const label = sectionLabel(slide.section);
      const lines = aligned?.[i]?.matched
        ? transposeLines(aligned[i]!.lines, shift, playKey)
        : slide.lyrics.split("\n").map((text) => ({ text, chords: [] }));
      // A section's slides stay together; a repeat (the chorus again) starts anew.
      const sectionKey = `${slide.section.type}${slide.section.number}`;
      const last = out[out.length - 1];
      if (last && lastSection === sectionKey && slide.order_in_section > 0) last.stanzas.push(lines);
      else out.push({ label, stanzas: [lines] });
      lastSection = sectionKey;
    });
    return out;
  }, [body, order, chart, writtenKey, playKey, showChords]);

  if (sections.length === 0) return <Empty>This song has no words yet.</Empty>;
  return (
    <div className="space-y-6 md:space-y-8" data-testid="song-words">
      {sections.map((s, si) => (
        <section key={si} aria-label={s.label}>
          <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground md:text-sm">{s.label}</h2>
          <div className="space-y-3 text-base leading-relaxed md:text-xl md:leading-relaxed">
            {s.stanzas.map((lines, li) => (
              <div key={li}>
                {lines.map((l, i) => (
                  <ChordLyrics key={i} text={l.text} chords={l.chords} showChords={showChords} chordClassName="font-semibold text-sky-400" />
                ))}
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

const CHORDS_KEY = "church-slides.web.chords";

function useShowChords(): [boolean, (v: boolean) => void] {
  const [show, setShow] = useState(() => {
    try {
      return localStorage.getItem(CHORDS_KEY) !== "off";
    } catch {
      return true;
    }
  });
  return [
    show,
    (v) => {
      setShow(v);
      try {
        localStorage.setItem(CHORDS_KEY, v ? "on" : "off");
      } catch {
        // Storage off: remembered for this visit only.
      }
    },
  ];
}

export function SongPage({ id, playKey, canEdit, back }: { id: string; playKey: string | null; canEdit: boolean; back: string }) {
  const { data, error } = useLibraryData(
    async (lib) => {
      const song = await lib.getSong(id);
      const stored = song ? await lib.getChart(id) : null;
      return { song, chart: parseStoredChart(stored?.chart_json), writtenKey: writtenKeyOf(stored) };
    },
    [id],
  );
  const [showChords, setShowChords] = useShowChords();
  useTitle(data?.song?.title ?? "Song");

  if (!data) return <PageHeader title="" back={back} />;
  const { song, chart, writtenKey } = data;
  if (!song) {
    return (
      <>
        <PageHeader title="Song" back={back} />
        <Empty>This song was deleted.</Empty>
      </>
    );
  }
  const hasChords = !!chart && chartHasChords(chart);
  const shownKey = playKey ?? writtenKey;
  const facts = [song.author, song.ccli ? `CCLI ${song.ccli}` : null, chart?.tempo ? `${chart.tempo} bpm` : null, chart?.time ?? null].filter(Boolean);

  return (
    <>
      <PageHeader
        title={song.title}
        back={back}
        wrap
        action={
          <>
            {hasChords && (
              <button
                type="button"
                className={cn(
                  "h-11 rounded-md border px-3 text-sm font-medium",
                  showChords ? "border-sky-400/50 bg-sky-400/10 text-sky-300" : "border-border text-muted-foreground hover:text-foreground",
                )}
                onClick={() => setShowChords(!showChords)}
                aria-pressed={showChords}
                data-testid="toggle-chords"
              >
                Chords
              </button>
            )}
            {canEdit && (
              <a href={href({ page: "edit-song", id })} className={iconButtonClass} aria-label="Edit song" title="Edit" data-testid="edit-song">
                <Pencil className="size-5" />
              </a>
            )}
          </>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      <div className="space-y-5 px-4 py-4">
        {(shownKey || facts.length > 0 || song.copyright) && (
          <div className="flex items-start gap-3">
            {shownKey && <KeyBadge songKey={shownKey} className="mt-0.5" />}
            <div className="min-w-0 flex-1 space-y-0.5 text-sm leading-relaxed text-muted-foreground">
              {facts.length > 0 && <div>{facts.join(" · ")}</div>}
              {playKey && writtenKey && playKey !== writtenKey && (
                <div>
                  In {playKey} for this service (written in {writtenKey})
                </div>
              )}
              {song.copyright && <div className="text-xs">{song.copyright}</div>}
            </div>
          </div>
        )}
        <SongWords
          body={song.body_text}
          order={song.slide_order}
          chart={chart}
          writtenKey={writtenKey}
          playKey={playKey}
          showChords={hasChords && showChords}
        />
      </div>
    </>
  );
}
