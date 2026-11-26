import { useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { BookOpen, GripVertical, MoreHorizontal, Music, Plus, Type } from "lucide-react";

import type { SetlistDetail, SetlistItemWithSong, SetlistRow } from "@/core/library";
import { KeyBadge } from "@/components/KeyBadge";
import { useLibrary } from "@/library/context";
import { KEY_CHOICES } from "@/lib/chords/chord";
import { itemTitle, readingBody, readingSlides, scriptureContent, textContent, textSlides } from "@/lib/serviceItemContent";
import { parseSlidesOverride } from "@/lib/slideSequence";
import { copiedService, nameForDate, shortServiceDate } from "@/lib/setlistName";
import { useServiceDateField } from "@/lib/useServiceDateField";
import { go, href } from "@/web/route";
import { matchesSearch } from "@/web/pages/SongsPage";
import { writtenKeyOf } from "@/web/pages/SongPage";
import {
  ConfirmSheet,
  DangerButton,
  Empty,
  ErrorLine,
  Field,
  PageHeader,
  PrimaryButton,
  QuietButton,
  Sheet,
  errorText,
  fieldClass,
  iconButtonClass,
  useLibraryData,
  useTitle,
} from "@/web/ui";
import { cn } from "@/lib/utils";

interface Loaded {
  service: SetlistDetail | null;
  /** Each song's written key, or null when it has chords but no key. Songs
   * without chords are left out: there's no key to change. */
  keys: Record<string, string | null>;
  /** The other services, to keep names unique and dates free. */
  others: SetlistRow[];
}

type Menu = { kind: "add" } | { kind: "service" } | { kind: "item"; id: string } | null;

export function ServicePage({
  id,
  canEdit,
  created = false,
  copied = false,
}: {
  id: string;
  canEdit: boolean;
  created?: boolean;
  /** Just made by "Copy for the next service": say so, or it looks like the same service. */
  copied?: boolean;
}) {
  const lib = useLibrary();
  const { data, error: loadError } = useLibraryData(
    async (l): Promise<Loaded> => {
      const service = await l.getSetlist(id);
      const keys: Record<string, string | null> = {};
      for (const item of service?.items ?? []) {
        if (!item.song_id || item.song_id in keys) continue;
        const chart = await l.getChart(item.song_id);
        if (chart) keys[item.song_id] = writtenKeyOf(chart);
      }
      const others = (await l.listSetlists()).filter((s) => s.id !== id);
      return { service, keys, others };
    },
    [id],
  );
  const [menu, setMenu] = useState<Menu>(null);
  const [error, setError] = useState<string | null>(null);
  // The order shown while a drag's save is on its way.
  const [dragged, setDragged] = useState<string[] | null>(null);
  useTitle(data?.service?.name ?? "Service");

  useEffect(() => setDragged(null), [data]);

  const run = async (f: () => Promise<unknown>) => {
    setError(null);
    try {
      await f();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const items = useMemo(() => {
    const all = data?.service?.items ?? [];
    if (!dragged) return all;
    const byId = new Map(all.map((i) => [i.id, i]));
    return dragged.map((i) => byId.get(i)).filter((i): i is SetlistItemWithSong => !!i);
  }, [data, dragged]);

  if (!data) return <PageHeader title="" back={href({ page: "services" })} />;
  const { service, keys, others } = data;
  const otherNames = others.map((s) => s.name);
  if (!service) {
    return (
      <>
        <PageHeader title="Service" back={href({ page: "services" })} />
        <Empty>This service was deleted.</Empty>
      </>
    );
  }

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return;
    const ids = items.map((i) => i.id);
    const next = arrayMove(ids, ids.indexOf(String(e.active.id)), ids.indexOf(String(e.over.id)));
    setDragged(next);
    void run(() => lib.reorderItems(service.id, next));
  };

  const open = menu?.kind === "item" ? items.find((i) => i.id === menu.id) : undefined;

  return (
    <>
      <PageHeader
        // An editor has the name in its own field just below: once is enough.
        title={canEdit ? "Service" : service.name || "Untitled service"}
        back={href({ page: "services" })}
        wrap
        action={
          canEdit && (
            <button type="button" className={iconButtonClass} aria-label="Service options" onClick={() => setMenu({ kind: "service" })}>
              <MoreHorizontal className="size-5" />
            </button>
          )
        }
      />
      {(error ?? loadError) && <ErrorLine>{error ?? loadError}</ErrorLine>}
      {copied && service.service_date && (
        <p className="border-b border-border bg-muted px-4 py-2.5 text-sm" role="status">
          Copied. This is the new service for {shortServiceDate(service.service_date)}.
        </p>
      )}
      {!canEdit && service.service_date && (
        <p className="px-4 pt-3 text-sm text-muted-foreground" data-testid="service-day">
          {shortServiceDate(service.service_date)}
        </p>
      )}
      {canEdit && <ServiceDetails key={service.id} service={service} otherNames={otherNames} focusName={created} onError={setError} />}

      {items.length === 0 ? (
        <Empty>Nothing in this service yet.</Empty>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd} accessibility={{ announcements: announce(items), screenReaderInstructions }}>
          <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
            <ol className="divide-y divide-border border-y border-border" data-testid="service-items">
              {items.map((item, n) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  number={n + 1}
                  serviceId={service.id}
                  writtenKey={item.song_id ? keys[item.song_id] : undefined}
                  canEdit={canEdit}
                  onOpen={() => setMenu({ kind: "item", id: item.id })}
                />
              ))}
            </ol>
          </SortableContext>
        </DndContext>
      )}
      {canEdit && (
        <div className="px-4 py-4">
          <PrimaryButton className="w-full" onClick={() => setMenu({ kind: "add" })} data-testid="add-to-service">
            <Plus className="size-4" /> Add
          </PrimaryButton>
        </div>
      )}

      {menu?.kind === "add" && <AddSheet serviceId={service.id} onClose={() => setMenu(null)} />}
      {menu?.kind === "service" && <ServiceMenu service={service} others={others} onClose={() => setMenu(null)} onError={setError} />}
      {open && (
        <ItemSheet
          item={open}
          serviceId={service.id}
          writtenKey={open.song_id && open.song_id in keys ? keys[open.song_id]! : undefined}
          onClose={() => setMenu(null)}
          onError={setError}
        />
      )}
    </>
  );
}

const screenReaderInstructions = {
  draggable: "To move an item, press space or enter, then the arrow keys, then space or enter again to drop it. Escape puts it back.",
};

/** What a screen reader hears while an item moves: titles and places, not ids. */
function announce(items: SetlistItemWithSong[]): Announcements {
  const name = (id: UniqueIdentifier) => {
    const item = items.find((i) => i.id === id);
    return item ? itemTitle(item, item.song) : "Item";
  };
  const place = (id: UniqueIdentifier | undefined) => `place ${items.findIndex((i) => i.id === id) + 1} of ${items.length}`;
  return {
    onDragStart: ({ active }) => `Picked up ${name(active.id)}, ${place(active.id)}.`,
    onDragOver: ({ active, over }) => (over ? `${name(active.id)} moved to ${place(over.id)}.` : undefined),
    onDragEnd: ({ active, over }) => (over ? `${name(active.id)} dropped at ${place(over.id)}.` : `${name(active.id)} put back.`),
    onDragCancel: ({ active }) => `${name(active.id)} put back.`,
  };
}

/** The name and date, edited in place. The name saves when you leave the
 * field or press Enter; the date as soon as it's picked, and a name still
 * made from the old date follows it. */
function ServiceDetails({
  service,
  otherNames,
  focusName,
  onError,
}: {
  service: SetlistDetail;
  otherNames: string[];
  focusName: boolean;
  onError: (e: string) => void;
}) {
  const lib = useLibrary();
  const [name, setName] = useState(service.name);
  const input = useRef<HTMLInputElement>(null);
  const editing = useRef(false);

  // Someone else renamed it (or the date did) while this field wasn't being edited.
  useEffect(() => {
    if (!editing.current) setName(service.name);
  }, [service.name]);

  useEffect(() => {
    if (!focusName) return;
    input.current?.focus();
    input.current?.select();
    // Once: a reload or Back shouldn't do it again.
    window.history.replaceState(null, "", href({ page: "service", id: service.id }));
  }, [focusName, service.id]);

  const saveName = async () => {
    editing.current = false;
    const next = name.trim();
    if (!next) return setName(service.name);
    if (next === service.name) return;
    try {
      await lib.renameSetlist(service.id, next);
    } catch (err) {
      onError(errorText(err));
    }
  };

  const nameNow = useRef(name);
  nameNow.current = name;
  const dateField = useServiceDateField(service.service_date, async (date) => {
    try {
      await lib.setServiceDate(service.id, date);
      const renamed = nameForDate(nameNow.current, date, otherNames);
      if (renamed !== nameNow.current) {
        nameNow.current = renamed;
        setName(renamed);
        await lib.renameSetlist(service.id, renamed);
      }
    } catch (err) {
      onError(errorText(err));
      throw err;
    }
  });

  return (
    <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row">
      <div className="min-w-0 flex-1">
      <Field label="Name">
        <input
          ref={input}
          className={fieldClass}
          value={name}
          onFocus={() => (editing.current = true)}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => void saveName()}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              setName(service.name);
              editing.current = false;
            }
          }}
          enterKeyHint="done"
          data-testid="service-name"
        />
      </Field>
      </div>
      <div className="shrink-0 sm:w-44">
        <Field label="Date">
          <input type="date" className={fieldClass} {...dateField} data-testid="service-date" />
        </Field>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  number,
  serviceId,
  writtenKey,
  canEdit,
  onOpen,
}: {
  item: SetlistItemWithSong;
  number: number;
  serviceId: string;
  /** The song's written key; undefined when it has no chords. */
  writtenKey: string | null | undefined;
  canEdit: boolean;
  onOpen: () => void;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id: item.id, disabled: !canEdit });
  const title = itemTitle(item, item.song);
  const key = item.kind === "song" && writtenKey !== undefined ? (item.song_key ?? writtenKey) : null;
  const Icon = item.kind === "scripture" ? BookOpen : item.kind === "text" ? Type : Music;
  const body = (
    <>
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{title}</span>
      {key && <KeyBadge songKey={key} className="size-7" />}
    </>
  );
  const rowClass = "flex min-h-14 min-w-0 flex-1 items-center gap-3 py-2 text-left";
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn("relative flex items-center bg-background hover:bg-muted/50", isDragging && "z-10 bg-muted shadow-lg")}
    >
      {canEdit ? (
        <button
          ref={setActivatorNodeRef}
          type="button"
          className="flex h-14 w-11 shrink-0 cursor-grab touch-none items-center justify-center text-muted-foreground hover:text-foreground active:cursor-grabbing"
          aria-label={`Move ${title} (item ${number})`}
          {...attributes}
          {...listeners}
        >
          <GripVertical className="size-4" />
        </button>
      ) : (
        <span className="w-11 shrink-0 text-center text-sm tabular-nums text-muted-foreground">{number}</span>
      )}
      {item.kind === "song" && item.song_id ? (
        <a href={href({ page: "song", id: item.song_id, key: item.song_key ?? null, from: serviceId })} className={rowClass}>
          {body}
        </a>
      ) : canEdit ? (
        <button type="button" className={rowClass} onClick={onOpen}>
          {body}
        </button>
      ) : (
        <div className={rowClass}>{body}</div>
      )}
      {canEdit ? (
        <button type="button" className={cn(iconButtonClass, "mx-0.5 text-muted-foreground hover:bg-transparent hover:text-foreground")} aria-label={`Options for ${title}`} onClick={onOpen}>
          <MoreHorizontal className="size-5" />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
    </li>
  );
}

/** One item's options: a song's key; a reading's passage; a text's title and
 * words. Removing it is here too. */
function ItemSheet({
  item,
  serviceId,
  writtenKey,
  onClose,
  onError,
}: {
  item: SetlistItemWithSong;
  serviceId: string;
  writtenKey: string | null | undefined;
  onClose: () => void;
  onError: (e: string) => void;
}) {
  const lib = useLibrary();
  const remove = () => {
    onClose();
    lib.removeItem(serviceId, item.id).catch((err: unknown) => onError(errorText(err)));
  };
  const save = (content: object, slides?: string | null) => {
    onClose();
    lib.setItemContent(serviceId, item.id, content, slides).catch((err: unknown) => onError(errorText(err)));
  };
  const title = itemTitle(item, item.song);

  if (item.kind === "scripture") return <ReadingSheet item={item} onSave={save} onRemove={remove} onClose={onClose} />;
  if (item.kind === "text") return <TextSheet item={item} onSave={save} onRemove={remove} onClose={onClose} />;
  return (
    <Sheet title={title} onClose={onClose}>
      <div className="space-y-5">
        {writtenKey !== undefined && (
          <Field label="Key for this service">
            <select
              className={fieldClass}
              defaultValue={item.song_key && item.song_key !== writtenKey ? item.song_key : ""}
              onChange={(e) => {
                const k = e.target.value || null;
                lib.setItemKey(serviceId, item.id, k).catch((err: unknown) => onError(errorText(err)));
              }}
              data-testid="item-key"
            >
              {(!writtenKey || !(KEY_CHOICES as readonly string[]).includes(writtenKey)) && (
                <option value="">{writtenKey ? `Original key (${writtenKey})` : "Original key"}</option>
              )}
              {KEY_CHOICES.map((k) => (
                <option key={k} value={k === writtenKey ? "" : k}>
                  {k === writtenKey ? `Original key (${k})` : k}
                </option>
              ))}
            </select>
          </Field>
        )}
        <DangerButton onClick={remove}>Remove from service</DangerButton>
      </div>
    </Sheet>
  );
}

type SaveItem = (content: object, slides?: string | null) => void;

function ReadingSheet({ item, onSave, onRemove, onClose }: { item: SetlistItemWithSong; onSave: SaveItem; onRemove: () => void; onClose: () => void }) {
  const content = scriptureContent(item);
  const [reference, setReference] = useState(content?.reference ?? "");
  const changed = reference.trim() !== (content?.reference ?? "");
  return (
    <Sheet title="Bible passage" onClose={onClose}>
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          // A new passage: the verses copied for the old one no longer fit.
          if (changed && reference.trim()) onSave({ reference: reference.trim(), ...(content?.translation ? { translation: content.translation } : {}) });
        }}
      >
        <Field label="Passage" hint="The slides computer fills in the verses from its Bibles.">
          <input className={fieldClass} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="John 3:16-17" />
        </Field>
        <PrimaryButton type="submit" className="w-full" disabled={!changed || !reference.trim()}>
          Save
        </PrimaryButton>
        <DangerButton onClick={onRemove}>Remove from service</DangerButton>
      </form>
    </Sheet>
  );
}

function TextSheet({ item, onSave, onRemove, onClose }: { item: SetlistItemWithSong; onSave: SaveItem; onRemove: () => void; onClose: () => void }) {
  const was = useMemo(() => {
    const t = textContent(item)?.title ?? "";
    const slides = parseSlidesOverride(item.slides_override)?.map((s) => s.lyrics) ?? [];
    // A text whose only slide is its title has no words of its own.
    const words = slides.length === 1 && slides[0]!.trim() === t.trim() ? "" : readingBody(slides);
    return { title: t, words };
  }, [item]);
  const [title, setTitle] = useState(was.title);
  const [words, setWords] = useState(was.words);
  const changed = title.trim() !== was.title || words !== was.words;
  return (
    <Sheet title="Text slide" onClose={onClose}>
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          const t = title.trim();
          if (changed && t) onSave({ title: t }, words.trim() ? readingSlides(words) : textSlides([t]));
        }}
      >
        <TextFields title={title} words={words} onTitle={setTitle} onWords={setWords} />
        <PrimaryButton type="submit" className="w-full" disabled={!changed || !title.trim()}>
          Save
        </PrimaryButton>
        <DangerButton onClick={onRemove}>Remove from service</DangerButton>
      </form>
    </Sheet>
  );
}

function TextFields({ title, words, onTitle, onWords }: { title: string; words: string; onTitle: (v: string) => void; onWords: (v: string) => void }) {
  return (
    <>
      <Field label="Title" hint="What the running order calls it, like Welcome or Notices.">
        <input className={fieldClass} value={title} onChange={(e) => onTitle(e.target.value)} />
      </Field>
      <Field label="Words" hint="A blank line starts a new slide. Leave empty to show the title.">
        <textarea className={`${fieldClass} h-auto min-h-32 py-2`} value={words} onChange={(e) => onWords(e.target.value)} />
      </Field>
    </>
  );
}

function ServiceMenu({
  service,
  others,
  onClose,
  onError,
}: {
  service: SetlistDetail;
  others: SetlistRow[];
  onClose: () => void;
  onError: (e: string) => void;
}) {
  const lib = useLibrary();
  const [confirming, setConfirming] = useState(false);
  const act = async (f: () => Promise<void>) => {
    onClose();
    try {
      await f();
    } catch (err) {
      onError(errorText(err));
    }
  };
  if (confirming) {
    return (
      <ConfirmSheet
        title="Delete this service?"
        action="Delete service"
        onClose={onClose}
        onAction={() =>
          void act(async () => {
            await lib.deleteSetlist(service.id);
            go({ page: "services" }, true);
          })
        }
      >
        “{service.name}” goes from every device. Its songs stay in the library.
      </ConfirmSheet>
    );
  }
  return (
    <Sheet title={service.name || "Service"} onClose={onClose}>
      <div className="space-y-3">
        <QuietButton
          className="w-full"
          onClick={() =>
            void act(async () => {
              // The copy is for the week after: dated and named for it.
              const { name, date } = copiedService(service, [...others, service]);
              const id = await lib.copySetlist(service.id, name, date);
              go({ page: "service", id, created: true, copied: true });
            })
          }
        >
          Copy for the next service
        </QuietButton>
        <DangerButton onClick={() => setConfirming(true)}>Delete service</DangerButton>
      </div>
    </Sheet>
  );
}

type AddKind = "song" | "scripture" | "text";

function AddSheet({ serviceId, onClose }: { serviceId: string; onClose: () => void }) {
  const lib = useLibrary();
  const [kind, setKind] = useState<AddKind>("song");
  const [query, setQuery] = useState("");
  const [reference, setReference] = useState("");
  const [title, setTitle] = useState("");
  const [words, setWords] = useState("");
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const { data: songs } = useLibraryData((l) => l.listSongs(), []);
  const shown = useMemo(() => (songs ?? []).filter((s) => matchesSearch(s, query)).slice(0, 50), [songs, query]);

  // The note belongs to the tab it was made on.
  const shownKind = useRef(kind);
  shownKind.current = kind;
  const add = async (f: () => Promise<unknown>, what: string): Promise<boolean> => {
    const from = kind;
    try {
      await f();
      if (shownKind.current === from) setNote({ text: `Added ${what}.`, error: false });
      return true;
    } catch (err) {
      if (shownKind.current === from) setNote({ text: errorText(err), error: true });
      return false;
    }
  };

  const tab = (k: AddKind, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={kind === k}
      className={cn("h-10 flex-1 rounded-md text-sm font-medium", kind === k ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground")}
      onClick={() => {
        setKind(k);
        setNote(null);
      }}
    >
      {label}
    </button>
  );

  return (
    <Sheet title="Add to service" onClose={onClose}>
      <div className="space-y-4">
        <div className="flex gap-1 rounded-lg border border-border p-1" role="tablist">
          {tab("song", "Song")}
          {tab("scripture", "Bible")}
          {tab("text", "Text slide")}
        </div>
        {kind === "song" && (
          <>
            <input type="search" className={fieldClass} placeholder="Search songs" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search songs" />
            <ul className="-mx-4 divide-y divide-border border-y border-border">
              {shown.map((s) => (
                <li key={s.id}>
                  <button type="button" className="min-h-11 w-full px-4 py-2.5 text-left hover:bg-muted/50" onClick={() => void add(() => lib.addSong(serviceId, s.id), s.title)}>
                    {s.title}
                  </button>
                </li>
              ))}
              {songs && shown.length === 0 && <li className="px-4 py-3 text-sm text-muted-foreground">No song matches.</li>}
            </ul>
          </>
        )}
        {kind === "scripture" && (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              const r = reference.trim();
              if (r) void add(() => lib.addServiceItem(serviceId, "scripture", { reference: r }), r).then((ok) => ok && setReference(""));
            }}
          >
            <Field label="Passage" hint="The slides computer fills in the verses from its Bibles.">
              <input className={fieldClass} placeholder="John 3:16-17" value={reference} onChange={(e) => setReference(e.target.value)} />
            </Field>
            <PrimaryButton type="submit" className="w-full" disabled={!reference.trim()}>
              Add passage
            </PrimaryButton>
          </form>
        )}
        {kind === "text" && (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              const t = title.trim();
              if (!t) return;
              void add(() => lib.addServiceItem(serviceId, "text", { title: t, body: words }), t).then((ok) => {
                if (!ok) return;
                setTitle("");
                setWords("");
              });
            }}
          >
            <TextFields title={title} words={words} onTitle={setTitle} onWords={setWords} />
            <PrimaryButton type="submit" className="w-full" disabled={!title.trim()}>
              Add text slide
            </PrimaryButton>
          </form>
        )}
        {/* Room kept for the note, so the list doesn't jump when it shows. */}
        <div className={cn("min-h-5 text-sm", note?.error ? "text-red-400" : "text-emerald-400")} role="status">
          {note?.text}
        </div>
      </div>
    </Sheet>
  );
}
