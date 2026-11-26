import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";

import type { SongRow } from "@/core/library";
import { KeyBadge } from "@/components/KeyBadge";
import { firstVerseLines } from "@/lib/slideSequence";
import { href } from "@/web/route";
import { writtenKeyOf } from "@/web/pages/SongPage";
import { Empty, ErrorLine, PageHeader, fieldClass, iconButtonClass, useLibraryData, useTitle } from "@/web/ui";

interface SongListRow extends SongRow {
  key: string | null;
}

const fold = (s: string) => s.toLowerCase().replace(/[’']/g, "");
const terms = (query: string) => fold(query).split(/\s+/).filter(Boolean);

/** Words and titles, every word must match somewhere (search by meaning
 * needs the slides computer's index). */
export function matchesSearch(song: Pick<SongRow, "title" | "body_text" | "author" | "ccli">, query: string): boolean {
  const words = terms(query);
  if (words.length === 0) return true;
  const hay = fold(`${song.title}\n${song.author ?? ""}\n${song.ccli ?? ""}\n${song.body_text}`);
  return words.every((w) => hay.includes(w));
}

/** The line of the words a search found, when it wasn't the title: shown
 * under the title so you can see why the song is there. */
export function matchingLine(song: Pick<SongRow, "title" | "body_text">, query: string): string | null {
  const words = terms(query);
  if (words.length === 0 || words.every((w) => fold(song.title).includes(w))) return null;
  const lines = song.body_text.split("\n").filter((l) => l.trim() && !/^\s*\[.*\]\s*$/.test(l));
  return lines.find((l) => words.some((w) => fold(l).includes(w)))?.trim() ?? null;
}

export function SongsPage({ canEdit }: { canEdit: boolean }) {
  const [query, setQuery] = useState("");
  const { data: songs, error } = useLibraryData(async (lib) => {
    const rows = await lib.listSongs();
    return Promise.all(rows.map(async (s): Promise<SongListRow> => ({ ...s, key: writtenKeyOf(await lib.getChart(s.id)) })));
  }, []);
  useTitle("Songs");

  const shown = useMemo(() => {
    if (!songs) return [];
    // Titles that match first, then songs whose words do.
    const hits = songs.filter((s) => matchesSearch(s, query));
    const q = fold(query.trim());
    return q ? [...hits.filter((s) => fold(s.title).includes(q)), ...hits.filter((s) => !fold(s.title).includes(q))] : hits;
  }, [songs, query]);

  return (
    <>
      <PageHeader
        title="Songs"
        action={
          canEdit && (
            <a href={href({ page: "edit-song", id: null })} className={iconButtonClass} aria-label="New song">
              <Plus className="size-5" />
            </a>
          )
        }
        below={
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              type="search"
              className={`${fieldClass} pl-9`}
              placeholder="Search titles and words"
              aria-label="Search titles and words"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setQuery("")}
              data-testid="song-search"
            />
          </div>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      {!songs ? null : songs.length === 0 ? (
        <Empty>No songs yet. Songs added here or on the slides computer show up on every device.</Empty>
      ) : shown.length === 0 ? (
        <Empty>No song matches “{query}”.</Empty>
      ) : (
        <ul className="divide-y divide-border">
          {shown.map((s) => (
            <li key={s.id}>
              <a href={href({ page: "song", id: s.id, key: null })} className="flex min-h-14 items-center gap-3 px-4 py-2.5 hover:bg-muted/50">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{s.title}</div>
                  <div className="truncate text-sm text-muted-foreground">
                    {matchingLine(s, query) ?? firstVerseLines(s.body_text, 1)}
                  </div>
                </div>
                {s.key && <KeyBadge songKey={s.key} />}
              </a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
