import { useEffect, useMemo, useRef, useState } from "react";

import type { NewSongInput, SongRow } from "@/core/library";
import { useLibrary } from "@/library/context";
import { deleteSongMessage } from "@/lib/deleteSongText";
import { parseSong } from "@/lib/parseSong";
import { formatSlideOrder, parseSlideOrder } from "@/lib/slideSequence";
import { go, goUnguarded, href, useLeaveGuard, type Route } from "@/web/route";
import { ConfirmSheet, DangerButton, ErrorLine, Field, PageHeader, PrimaryButton, errorText, fieldClass, useTitle } from "@/web/ui";

const BLANK: NewSongInput = { title: "", body_text: "", author: "", ccli: "", copyright: "", slide_order: "" };

function formOf(song: SongRow): NewSongInput {
  return {
    title: song.title,
    body_text: song.body_text,
    author: song.author ?? "",
    ccli: song.ccli ?? "",
    copyright: song.copyright ?? "",
    slide_order: song.slide_order ?? "",
  };
}

const same = (a: NewSongInput, b: NewSongInput) =>
  (Object.keys(BLANK) as (keyof NewSongInput)[]).every((k) => (a[k] ?? "") === (b[k] ?? ""));

/** Tags in an Order that name no section of the words, as typed. */
export function unknownOrderTags(order: string, body: string): string[] {
  const have = new Set(parseSong(body).slides.map((s) => `${s.section.type}${s.section.number}`));
  const tokens = order.split(/[,\s]+/).filter(Boolean);
  const parsed = parseSlideOrder(order);
  return tokens.filter((_, i) => {
    const s = parsed[i];
    return !s || !have.has(`${s.type}${s.number}`);
  });
}

type Ask = { kind: "leave"; to: Route } | { kind: "delete"; usedBy: string[] } | null;

/** Words, order and credits. Chords are edited on the slides computer for
 * now; they stay with the song and are lined up again with the new words. */
export function SongEditor({ id }: { id: string | null }) {
  const lib = useLibrary();
  const [opened, setOpened] = useState<SongRow | null>(null);
  const [form, setForm] = useState<NewSongInput | null>(id ? null : BLANK);
  const [hasChart, setHasChart] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [ask, setAsk] = useState<Ask>(null);
  const titleInput = useRef<HTMLInputElement>(null);
  useTitle(id ? "Edit song" : "New song");

  useEffect(() => {
    if (!id) return;
    void (async () => {
      const song = await lib.getSong(id);
      if (!song) {
        setError("This song was deleted.");
        return;
      }
      setOpened(song);
      setForm(formOf(song));
      setHasChart(!!(await lib.getChart(id)));
    })();
  }, [id, lib]);

  const dirty = !!form && !saving && !same(form, opened ? formOf(opened) : BLANK);
  useLeaveGuard(dirty, (to) => setAsk({ kind: "leave", to }));

  const unknownTags = useMemo(() => (form?.slide_order ? unknownOrderTags(form.slide_order, form.body_text) : []), [form?.slide_order, form?.body_text]);
  const sections = useMemo(() => (form ? formatSlideOrder(parseSong(form.body_text).section_order) : ""), [form?.body_text]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: keyof NewSongInput, clean: (v: string) => string = (v) => v) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const v = clean(e.target.value);
    setForm((f) => (f ? { ...f, [k]: v } : f));
    if (k === "title") setTitleError(null);
  };

  const back = id ? href({ page: "song", id, key: null }) : href({ page: "songs" });

  const save = async () => {
    if (!form) return;
    if (!form.title.trim()) {
      setTitleError("A song needs a title.");
      titleInput.current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const input = { ...form, default_tempo_wps: opened?.default_tempo_wps ?? null };
      if (id) {
        await lib.updateSong(id, input, opened ?? undefined);
        go({ page: "song", id, key: null }, true);
      } else {
        const newId = await lib.createSong(input);
        go({ page: "song", id: newId, key: null }, true);
      }
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  };

  /** Ask first, saying which services use it (they'd have to drop it first). */
  const askDelete = async () => {
    if (!id) return;
    const usedBy: string[] = [];
    for (const s of await lib.listSetlists()) {
      const detail = await lib.getSetlist(s.id);
      if (detail?.items.some((i) => i.song_id === id)) usedBy.push(s.name || "Untitled service");
    }
    setAsk({ kind: "delete", usedBy });
  };

  const remove = async () => {
    if (!id) return;
    setAsk(null);
    try {
      setSaving(true);
      await lib.deleteSong(id);
      go({ page: "songs" }, true);
    } catch (err) {
      setSaving(false);
      setError(errorText(err));
    }
  };

  return (
    <>
      <PageHeader
        title={id ? "Edit song" : "New song"}
        back={back}
        action={
          <PrimaryButton onClick={() => void save()} disabled={!form || saving} data-testid="save-song">
            Save
          </PrimaryButton>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      {form && (
        <div className="space-y-5 px-4 py-5">
          <Field label="Title" error={titleError}>
            <input
              ref={titleInput}
              className={fieldClass}
              value={form.title}
              onChange={set("title")}
              autoFocus={!id}
              aria-invalid={!!titleError}
              data-testid="song-title"
            />
          </Field>
          <Field
            label="Words"
            hint={
              <>
                A line like [Verse 1] starts a section; a blank line starts a new slide.
                {hasChart && " The chords stay with the song."}
              </>
            }
          >
            <textarea
              className={`${fieldClass} h-auto min-h-[50dvh] py-2 font-mono text-sm leading-relaxed`}
              value={form.body_text}
              onChange={set("body_text")}
              spellCheck={false}
              placeholder={"[Verse 1]\n…\n\n[Chorus]\n…"}
              data-testid="song-body"
            />
          </Field>
          <Field
            label="Order"
            error={unknownTags.length ? `Not in the words: ${unknownTags.join(", ")}` : null}
            hint={
              <>
                The order it&apos;s sung, using the sections above
                {sections ? ` (${sections})` : ""}: V1 is [Verse 1], C is [Chorus], B is [Bridge]. Leave empty to sing each once.
              </>
            }
          >
            <input
              className={fieldClass}
              value={form.slide_order ?? ""}
              onChange={set("slide_order")}
              aria-invalid={unknownTags.length > 0}
              autoCapitalize="characters"
              data-testid="song-order"
            />
          </Field>
          <Field label="Author">
            <input className={fieldClass} value={form.author ?? ""} onChange={set("author")} />
          </Field>
          <Field label="CCLI">
            <input className={fieldClass} value={form.ccli ?? ""} onChange={set("ccli", (v) => v.replace(/\D/g, ""))} inputMode="numeric" />
          </Field>
          <Field label="Copyright">
            <input className={fieldClass} value={form.copyright ?? ""} onChange={set("copyright")} />
          </Field>
          {id && (
            <div className="pt-4">
              <DangerButton onClick={() => void askDelete()} disabled={saving}>
                Delete song
              </DangerButton>
            </div>
          )}
        </div>
      )}

      {ask?.kind === "leave" && (
        <ConfirmSheet title="Discard your changes?" action="Discard changes" onClose={() => setAsk(null)} onAction={() => goUnguarded(ask.to)}>
          You changed this song but didn&apos;t save it.
        </ConfirmSheet>
      )}
      {ask?.kind === "delete" && (
        <ConfirmSheet title="Delete this song?" action="Delete song" onClose={() => setAsk(null)} onAction={() => void remove()}>
          {deleteSongMessage(ask.usedBy)}
        </ConfirmSheet>
      )}
    </>
  );
}
