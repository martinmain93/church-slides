import { describe, expect, it } from "vitest";

import { ApiError, OfflineError, socketUrlOk } from "@/lib/account/browserSignIn";
import { webLibrary } from "@/library/web";
import { memoryPersist } from "@/library/web/persist";
import { RecordStore, type PushChange } from "@/library/web/store";
import { LibrarySync, type Api } from "@/library/web/sync";
import { applySet, type Fields } from "@/core/records";
import { parseStoredChart } from "@/lib/chords/chart";
import { textSlides } from "@/lib/serviceItemContent";
import { deleteSongMessage } from "@/lib/deleteSongText";
import { copiedService, groupServices, lastService, nameForDate, newService, nextServiceDate, rowDate } from "@/lib/setlistName";
import { unknownOrderTags } from "@/web/pages/SongEditor";
import { matchingLine } from "@/web/pages/SongsPage";
import { href, parseRoute } from "@/web/route";

// Public-domain words only (AGENTS.md).
const AMAZING = "[Verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me\n\n[Verse 2]\nTwas grace that taught my heart to fear";

/** A server that applies the protocol's rules: one change order, the last
 * change wins field by field, words made on an older version are kept as a
 * revision, refs keep songs alive. */
class FakeServer {
  seq = 0;
  records = new Map<string, { type: string; id: string; seq: number; deleted: boolean; fields: Fields; bodySeq: number }>();
  revisions: string[] = [];
  offline = false;
  role: "operator" | "set_leader" | "member" = "operator";
  pushes = 0;
  pulls = 0;

  api: Api = async <T,>(method: string, path: string, body?: unknown): Promise<T> => {
    if (this.offline) throw new OfflineError("Couldn't reach Church Slides.");
    if (method === "POST" && path.endsWith("/sync/push")) {
      this.pushes++;
      if (this.role === "member") throw new ApiError("Your role in this organization doesn't allow that.", 403, "forbidden");
      return this.push(body as { changes: PushChange[] }) as T;
    }
    if (method === "GET" && path.includes("/sync/pull")) return this.pull(Number(new URL(`http://x${path}`).searchParams.get("after"))) as T;
    throw new Error(`unexpected ${method} ${path}`);
  };

  private push({ changes }: { changes: PushChange[] }) {
    const results = changes.map((c) => {
      if (this.role === "set_leader" && c.type === "song") return { op: c.op, status: "refused", code: "forbidden", message: "" };
      const k = `${c.type}/${c.id}`;
      const old = this.records.get(k);
      if (c.delete) {
        const usedBy = [...this.records.values()].some(
          (r) => !r.deleted && Array.isArray(r.fields.refs) && (r.fields.refs as string[]).includes(`${c.type}:${c.id}`),
        );
        if (usedBy) return { op: c.op, status: "refused", code: "in_use", message: "" };
        this.seq++;
        this.records.set(k, { ...old!, seq: this.seq, deleted: true });
        return { op: c.op, status: "applied", seq: this.seq };
      }
      let conflict = false;
      if (old && c.set && "body_text" in c.set && c.base < old.bodySeq) {
        this.revisions.push(String(old.fields.body_text));
        conflict = true;
      }
      this.seq++;
      const fields = applySet(old?.fields ?? {}, c.set ?? {});
      this.records.set(k, {
        type: c.type,
        id: c.id,
        seq: this.seq,
        deleted: false,
        fields,
        bodySeq: c.set && "body_text" in c.set ? this.seq : (old?.bodySeq ?? 0),
      });
      return { op: c.op, status: "applied", seq: this.seq, conflict };
    });
    return { results, latest: this.seq };
  }

  /** Forget everything, as a server restored from nothing would. */
  wipe() {
    this.records.clear();
    this.seq = 0;
  }

  private pull(after: number) {
    this.pulls++;
    const records = [...this.records.values()]
      .filter((r) => r.seq > after)
      .sort((a, b) => a.seq - b.seq)
      .map(({ type, id, seq, deleted, fields }) => ({ type, id, seq, deleted, fields }));
    return { records, seq: this.seq, more: false };
  }
}

async function device(server: FakeServer) {
  const store = await RecordStore.open(memoryPersist());
  const sync = new LibrarySync(store, server.api, "org1", "Grace Chapel");
  const lib = webLibrary(store, { changedHere: () => undefined, onChange: (l) => sync.onChange(l) });
  return { store, sync, lib };
}

describe("the web app's library", () => {
  it("a song made on one device shows on another", async () => {
    const server = new FakeServer();
    const phone = await device(server);
    const laptop = await device(server);
    const id = await phone.lib.createSong({ title: "Amazing Grace", body_text: AMAZING, author: "John Newton" });
    expect(phone.store.waiting).toBe(1);
    await phone.sync.syncNow();
    expect(phone.store.waiting).toBe(0);
    await laptop.sync.syncNow();
    const song = await laptop.lib.getSong(id);
    expect(song?.title).toBe("Amazing Grace");
    expect(song?.author).toBe("John Newton");
    expect(id).toMatch(/^song_[0-9a-z]{26}$/);
  });

  it("two devices editing different fields offline both keep their change", async () => {
    const server = new FakeServer();
    const a = await device(server);
    const b = await device(server);
    const id = await a.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await a.sync.syncNow();
    await b.sync.syncNow();

    await a.lib.updateSong(id, { title: "Amazing Grace (My Chains Are Gone)", body_text: AMAZING });
    await b.lib.updateSong(id, { title: "Amazing Grace", body_text: AMAZING, ccli: "22025" });
    await a.sync.syncNow();
    await b.sync.syncNow();
    await a.sync.syncNow();
    for (const d of [a, b]) {
      const s = await d.lib.getSong(id);
      expect(s?.title).toBe("Amazing Grace (My Chains Are Gone)");
      expect(s?.ccli).toBe("22025");
    }
    expect(server.revisions).toEqual([]);
  });

  it("words edited on an older version keep the other words as a revision", async () => {
    const server = new FakeServer();
    const a = await device(server);
    const b = await device(server);
    const id = await a.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await a.sync.syncNow();
    await b.sync.syncNow();

    await a.lib.updateSong(id, { title: "Amazing Grace", body_text: `${AMAZING}\nAnd grace my fears relieved` });
    await a.sync.syncNow();
    // b edits the words without having pulled a's: its change is on the older version.
    await b.lib.updateSong(id, { title: "Amazing Grace", body_text: `${AMAZING}\nHow precious did that grace appear` });
    await b.sync.syncNow();
    expect(server.revisions).toHaveLength(1);
    expect(server.revisions[0]).toContain("fears relieved");
  });

  it("an edit to the words keeps the song's chords and other data", async () => {
    const server = new FakeServer();
    const a = await device(server);
    const id = "song_withchart";
    server.records.set(`song/${id}`, {
      type: "song",
      id,
      seq: ++server.seq,
      deleted: false,
      bodySeq: server.seq,
      fields: {
        title: "Amazing Grace",
        body_text: AMAZING,
        created_at: 1,
        updated_at: 1,
        chart: { chart_json: '{"version":1,"key":"G","tempo":null,"time":null,"sections":[]}', original_key: "G" },
        scriptures: [{ id: "x", reference: "Ephesians 2:8" }],
        some_newer_field: 7,
      },
    });
    await a.sync.syncNow();
    expect((await a.lib.getChart(id))?.original_key).toBe("G");
    await a.lib.updateSong(id, { title: "Amazing Grace", body_text: `${AMAZING}\nI once was lost` });
    const sent = a.store.nextPush();
    expect(Object.keys(sent[0]!.set!).sort()).toEqual(["body_text", "updated_at"]);
    await a.sync.syncNow();
    const f = server.records.get(`song/${id}`)!.fields;
    expect(f.chart).toBeTruthy();
    expect(f.scriptures).toBeTruthy();
    expect(f.some_newer_field).toBe(7);
  });

  it("a service is planned offline and goes up once back online", async () => {
    const server = new FakeServer();
    const phone = await device(server);
    const songId = await phone.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await phone.sync.syncNow();

    server.offline = true;
    const setId = await phone.lib.createSetlist("Sunday 29 Nov");
    const item = await phone.lib.addSong(setId, songId);
    await phone.lib.addServiceItem(setId, "scripture", { reference: "John 3:16" });
    await phone.lib.addServiceItem(setId, "text", { title: "Welcome", body: "Welcome\n\nNotices" }, null);
    await phone.lib.setItemKey(setId, item, "A");
    await phone.sync.syncNow();
    expect(phone.sync.getStatus().state).toBe("offline");
    expect(phone.sync.getStatus().waiting).toBe(1);

    server.offline = false;
    await phone.sync.syncNow();
    expect(phone.sync.getStatus()).toMatchObject({ state: "ok", waiting: 0 });
    const f = server.records.get(`setlist/${setId}`)!.fields;
    expect(f.refs).toEqual([`song:${songId}`]);
    const items = f.items as Record<string, unknown>[];
    expect(items.map((i) => i.kind)).toEqual(["song", "scripture", "text"]);
    expect(items.map((i) => i.position)).toEqual([0, 1, 2]);
    expect(items[0]!.song_key).toBe("A");
    expect(JSON.parse(String(items[2]!.slides_override)).map((s: { lyrics: string }) => s.lyrics)).toEqual(["Welcome", "Notices"]);

    const laptop = await device(server);
    await laptop.sync.syncNow();
    const detail = await laptop.lib.getSetlist(setId);
    expect(detail?.items.map((i) => i.song?.title ?? i.kind)).toEqual(["Amazing Grace", "scripture", "text"]);
  });

  it("reordering and removing keep positions 0, 1, 2…", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const s1 = await d.lib.createSong({ title: "A", body_text: "one" });
    const s2 = await d.lib.createSong({ title: "B", body_text: "two" });
    const s3 = await d.lib.createSong({ title: "C", body_text: "three" });
    const set = await d.lib.createSetlist("Evening");
    const i1 = await d.lib.addSong(set, s1);
    const i2 = await d.lib.addSong(set, s2);
    const i3 = await d.lib.addSong(set, s3);
    await d.lib.reorderItems(set, [i3, i1, i2]);
    await d.lib.removeItem(set, i1);
    const detail = await d.lib.getSetlist(set);
    expect(detail?.items.map((i) => [i.song?.title, i.position])).toEqual([
      ["C", 0],
      ["B", 1],
    ]);
  });

  it("deleting a song takes it out of the services that have it, as on the desktop", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const songId = await d.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    const other = await d.lib.createSong({ title: "Holy, Holy, Holy", body_text: "[Verse 1]\nHoly, holy, holy" });
    const set = await d.lib.createSetlist("Sunday");
    await d.lib.addSong(set, songId);
    await d.lib.addSong(set, other);
    await d.lib.deleteSong(songId);
    expect(await d.lib.getSong(songId)).toBeNull();
    expect((await d.lib.getSetlist(set))!.items.map((i) => i.song_id)).toEqual([other]);
    expect(deleteSongMessage(["Sunday 6 Dec"])).toMatch(/It's in 1 service \(Sunday 6 Dec\)\. It comes out of them/);
  });

  it("a change the server refuses is undone here, with the reason shown", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const id = await d.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await d.sync.syncNow();
    server.role = "set_leader";
    await d.lib.updateSong(id, { title: "Renamed", body_text: AMAZING });
    let changed = 0;
    d.sync.onChange(() => changed++);
    await d.sync.syncNow();
    expect((await d.lib.getSong(id))?.title).toBe("Amazing Grace");
    expect(d.sync.getStatus().message).toMatch(/your role can't edit that in Grace Chapel/);
    expect(changed).toBeGreaterThan(0);
  });

  it("someone else's edit to another field isn't overwritten by a change waiting here", async () => {
    const server = new FakeServer();
    const a = await device(server);
    const b = await device(server);
    const id = await a.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await a.sync.syncNow();
    await b.sync.syncNow();
    server.offline = true;
    await b.lib.updateSong(id, { title: "Amazing Grace", body_text: AMAZING, author: "John Newton" });
    server.offline = false;
    await a.lib.updateSong(id, { title: "Amazing Grace", body_text: AMAZING, copyright: "Public domain" });
    await a.sync.syncNow();
    // b pulls a's copyright before its own author goes up; neither is lost.
    await b.sync.syncNow();
    await a.sync.syncNow();
    for (const d of [a, b]) {
      const s = await d.lib.getSong(id);
      expect([s?.author, s?.copyright]).toEqual(["John Newton", "Public domain"]);
    }
  });

  it("a deleted record made here and never sent is simply forgotten", async () => {
    const d = await device(new FakeServer());
    const id = await d.lib.createSong({ title: "Draft", body_text: "" });
    await d.lib.deleteSong(id);
    expect(d.store.waiting).toBe(0);
    expect(await d.lib.getSong(id)).toBeNull();
  });

  it("keeps the library and unsent changes between visits", async () => {
    const persist = memoryPersist();
    const server = new FakeServer();
    const store = await RecordStore.open(persist);
    const lib = webLibrary(store, { changedHere: () => undefined, onChange: () => () => undefined });
    const id = await lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    const again = await RecordStore.open(persist);
    expect(again.device).toBe(store.device);
    expect(again.waiting).toBe(1);
    const sync = new LibrarySync(again, server.api, "org1", "Grace Chapel");
    await sync.syncNow();
    expect(server.records.get(`song/${id}`)?.fields.title).toBe("Amazing Grace");
  });

  // --- Fixes after the 2026-11-25 review ---------------------------------------

  it("an editor opened before a pull sends only what it changed, on the version it opened", async () => {
    const server = new FakeServer();
    const web = await device(server);
    const desk = await device(server);
    const id = await desk.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await desk.sync.syncNow();
    await web.sync.syncNow();

    const opened = (await web.lib.getSong(id))!; // the editor opens
    await desk.lib.updateSong(id, { title: "Amazing Grace", body_text: `${AMAZING}\nI once was lost` });
    await desk.sync.syncNow();
    await web.sync.syncNow(); // the desktop's words arrive while it's open

    // Only the author was touched in the form; its words are the old ones.
    await web.lib.updateSong(id, { ...opened, author: "John Newton" }, opened);
    const sent = web.store.nextPush();
    expect(sent[0]!.set!.author).toBe("John Newton");
    expect(Object.keys(sent[0]!.set!).filter((k) => k !== "updated_at")).toEqual(["author"]);
    await web.sync.syncNow();
    expect(server.records.get(`song/${id}`)!.fields.body_text).toContain("I once was lost");

    // Changing the words too keeps the desktop's as a revision.
    const again = (await desk.lib.getSong(id))!;
    await desk.lib.updateSong(id, { ...again, body_text: `${AMAZING}\nBut now am found` });
    await desk.sync.syncNow();
    await web.lib.updateSong(id, { ...opened, body_text: `${AMAZING}\nWas blind but now I see` }, opened);
    await web.sync.syncNow();
    expect(server.revisions.at(-1)).toContain("But now am found");
  });

  it("a record deleted while its create is on its way up stays deleted", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const id = await d.lib.createSetlist("Sunday 29 Nov", "2026-11-29");
    const batch = d.store.nextPush(); // the create goes up…
    await d.lib.deleteSetlist(id); // …and it's deleted before the answer
    const resp = (server.api as Api)<{ results: never[] }>("POST", "/orgs/org1/sync/push", { device: d.store.device, changes: batch });
    await d.store.afterPush(batch, (await resp).results);
    expect(d.store.waiting).toBe(1);
    await d.sync.syncNow();
    expect(server.records.get(`setlist/${id}`)?.deleted).toBe(true);
    expect(await d.lib.getSetlist(id)).toBeNull();
  });

  it("after the server's library is reset, waiting edits go up as whole records and the pages refresh", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const id = await d.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await d.sync.syncNow();
    await d.lib.updateSong(id, { title: "Amazing Grace (Hymn)", body_text: AMAZING });
    expect(Object.keys(d.store.nextPush()[0]!.set!)).not.toContain("body_text");
    // The server's numbers went backwards (restored): download again.
    server.wipe();
    await d.store.reset();
    const f = d.store.nextPush()[0]!.set!;
    expect(f).toMatchObject({ title: "Amazing Grace (Hymn)", body_text: AMAZING });
    expect(f.created_at).toBeTypeOf("number");

    // And through the sync loop: a pull whose number is below ours resets.
    const e = await device(server);
    await e.sync.syncNow();
    let changed = 0;
    e.sync.onChange(() => changed++);
    e.store.cursor = 50;
    await e.sync.syncNow();
    expect(e.store.cursor).toBe(server.seq);
    expect(changed).toBeGreaterThan(0);
  });

  it("a push the role can't make at all is refused once, and pulling still happens", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const id = await d.lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    await d.sync.syncNow();
    server.role = "member";
    await d.lib.updateSong(id, { title: "Renamed", body_text: AMAZING });
    const pulls = server.pulls;
    await d.sync.syncNow();
    expect(server.pushes).toBeLessThanOrEqual(3);
    expect(server.pulls).toBeGreaterThan(pulls);
    expect(d.store.waiting).toBe(0);
    expect((await d.lib.getSong(id))?.title).toBe("Amazing Grace");
    expect(d.sync.getStatus()).toMatchObject({ state: "ok" });
    expect(d.sync.getStatus().message).toMatch(/your role/);
  });

  it("renaming a service the desktop made sends only the name, and keeps item fields it doesn't know", async () => {
    const server = new FakeServer();
    const d = await device(server);
    const id = "setlist_fromdesk";
    // As serde_json sends them: keys sorted, plus a field from a newer app.
    server.records.set(`setlist/${id}`, {
      type: "setlist",
      id,
      seq: ++server.seq,
      deleted: false,
      bodySeq: 0,
      fields: {
        created_at: 1,
        items: [{ id: "item_1", kind: "text", content: '{"title":"Welcome"}', position: 0, slides_override: "[]", sparkle: true }],
        name: "Sunday",
      },
    });
    await d.sync.syncNow();
    await d.lib.renameSetlist(id, "Sunday morning");
    expect(d.store.nextPush()[0]!.set).toEqual({ name: "Sunday morning" });
    await d.lib.setServiceDate(id, "2026-11-29");
    await d.lib.reorderItems(id, ["item_1"]);
    await d.sync.syncNow();
    const f = server.records.get(`setlist/${id}`)!.fields;
    expect(f).toMatchObject({ name: "Sunday morning", service_date: "2026-11-29" });
    expect((f.items as Record<string, unknown>[])[0]!.sparkle).toBe(true);
  });

  it("a text item's slides are made as the desktop makes them", async () => {
    const d = await device(new FakeServer());
    const set = await d.lib.createSetlist("Sunday");
    const bare = await d.lib.addServiceItem(set, "text", { title: "Welcome", body: "" });
    const worded = await d.lib.addServiceItem(set, "text", { title: "Notices", body: "Coffee after\n\nPrayer at six" });
    const items = (await d.lib.getSetlist(set))!.items;
    const slides = (itemId: string) => JSON.parse(items.find((i) => i.id === itemId)!.slides_override!) as { lyrics: string }[];
    expect(items.find((i) => i.id === bare)!.slides_override).toBe(textSlides(["Welcome"]));
    expect(slides(worded).map((s) => s.lyrics)).toEqual(["Coffee after", "Prayer at six"]);

    await d.lib.setItemContent(set, worded, { title: "Notices" }, textSlides(["Coffee after"]));
    expect(JSON.parse((await d.lib.getSetlist(set))!.items[1]!.slides_override!)).toHaveLength(1);
  });

  it("someone else's library left in this browser is wiped when another person opens it", async () => {
    const persist = memoryPersist();
    const alice = await RecordStore.open(persist, "user_alice");
    const lib = webLibrary(alice, { changedHere: () => undefined, onChange: () => () => undefined });
    await lib.createSong({ title: "Amazing Grace", body_text: AMAZING });
    expect((await RecordStore.open(persist, "user_alice")).waiting).toBe(1);
    const bob = await RecordStore.open(persist, "user_bob");
    expect(bob.waiting).toBe(0);
    expect(bob.all("song")).toEqual([]);
  });
});

describe("services' dates and names", () => {
  it("a new service is for the first free Sunday, and never shares a name", () => {
    const wed = new Date(2026, 10, 25, 9); // Wednesday 25 Nov 2026
    expect(nextServiceDate(wed)).toBe("2026-11-29");
    expect(newService([], wed)).toEqual({ name: "Sunday 29 Nov", date: "2026-11-29" });
    // The coming Sunday has one: the next is the Sunday after.
    expect(newService([{ name: "Sunday 29 Nov", service_date: "2026-11-29" }], wed)).toEqual({ name: "Sunday 6 Dec", date: "2026-12-06" });
    // An old undated service with that name doesn't take the day, only the name.
    expect(newService([{ name: "Sunday 29 Nov" }], wed)).toEqual({ name: "Sunday 29 Nov (2)", date: "2026-11-29" });
  });

  it("a copy is for the week after the service it copies", () => {
    const wed = new Date(2026, 10, 25, 9);
    const dec6 = { name: "Sunday 6 Dec", service_date: "2026-12-06" };
    expect(copiedService(dec6, [dec6], wed)).toEqual({ name: "Sunday 13 Dec", date: "2026-12-13" });
    // A typed name is kept (made unique); a past service copies to the coming Sunday.
    const carols = { name: "Carol service", service_date: "2025-12-14" };
    expect(copiedService(carols, [carols], wed)).toEqual({ name: "Carol service (2)", date: "2026-11-29" });
    // Taken weeks are skipped.
    const dec13 = { name: "Sunday 13 Dec", service_date: "2026-12-13" };
    expect(copiedService(dec6, [dec6, dec13], wed).date).toBe("2026-12-20");
  });

  it("a date name follows the date, however it got there; a typed one stays", () => {
    expect(nameForDate("Sunday 29 Nov", "2026-12-02", [])).toBe("Wednesday 2 Dec");
    expect(nameForDate("Wednesday 2 Dec", "2026-12-13", [])).toBe("Sunday 13 Dec");
    expect(nameForDate("Sunday 29 Nov (2)", "2026-12-06", ["Sunday 6 Dec"])).toBe("Sunday 6 Dec (2)");
    expect(nameForDate("Carol service", "2026-12-20", [])).toBe("Carol service");
  });

  it("a list shows the date only when the name doesn't already say it", () => {
    const now = new Date(2026, 10, 25);
    expect(rowDate({ name: "Sunday 6 Dec", service_date: "2026-12-06" }, now)).toBeNull();
    expect(rowDate({ name: "Carol service", service_date: "2026-12-20" }, now)).toBe("Sun 20 Dec");
    expect(rowDate({ name: "Sunday 3 Jan", service_date: "2027-01-03" }, now)).toBe("2027");
  });

  it("'copy last service' copies the latest one held, not a draft for next month", () => {
    const row = (id: string, service_date: string) => ({ id, name: id, service_date, created_at: 0, last_used_at: null });
    expect(lastService([row("next month", "2026-12-27"), row("last week", "2026-11-22"), row("before", "2026-11-15")], "2026-11-25")?.id).toBe("last week");
  });

  it("the services list puts coming services first, soonest at the top", () => {
    const row = (id: string, service_date: string | null, last_used_at: number | null = null) => ({ id, name: id, service_date, created_at: 0, last_used_at });
    const { upcoming, past } = groupServices(
      [
        row("old", "2026-11-01"),
        row("next", "2026-11-29"),
        row("later", "2026-12-06"),
        row("undated-run", null, new Date(2026, 9, 1, 12).getTime()),
        row("undated-planned", null),
      ],
      "2026-11-25",
    );
    expect(upcoming.map((s) => s.id)).toEqual(["next", "later", "undated-planned"]);
    expect(past.map((s) => s.id)).toEqual(["old", "undated-run"]);
  });
});

describe("the web app's pieces", () => {
  it("a song link keeps its key and service, and a malformed one doesn't crash", () => {
    const link = href({ page: "song", id: "song_1", key: "A", from: "setlist_9", item: "item_3" });
    expect(parseRoute(link)).toEqual({ page: "song", id: "song_1", key: "A", from: "setlist_9", item: "item_3" });
    expect(parseRoute("#/songs/%E0")).toMatchObject({ page: "song", id: "%E0" });
    expect(parseRoute(href({ page: "service", id: "s", created: true }))).toEqual({ page: "service", id: "s", created: true, copied: false });
  });

  it("the live socket must be on the account server", () => {
    expect(socketUrlOk("wss://api.churchslides.app/v1/orgs/x/sync/socket?t=1", "https://api.churchslides.app")).toBe(true);
    expect(socketUrlOk("wss://evil.example/socket", "https://api.churchslides.app")).toBe(false);
    expect(socketUrlOk("ws://api.churchslides.app/socket", "https://api.churchslides.app")).toBe(false);
    expect(socketUrlOk("ws://localhost:8787/socket", "http://localhost:8787")).toBe(true);
  });

  it("an Order naming sections the words don't have is caught", () => {
    expect(unknownOrderTags("V1, C, V2, X9", AMAZING)).toEqual(["C", "X9"]);
    expect(unknownOrderTags("V1 V2 V1", AMAZING)).toEqual([]);
  });

  it("a malformed chart from another device is cleaned, not crashed on", () => {
    const c = parseStoredChart('{"key":"G","sections":[{},{"label":"V","stanzas":[[{"text":"Amazing","chords":[{"pos":0,"chord":"G"},{}]}],"x"]}]}');
    expect(c?.sections).toEqual([
      { label: "", stanzas: [] },
      { label: "V", stanzas: [[{ text: "Amazing", chords: [{ pos: 0, chord: "G" }] }]] },
    ]);
    expect(parseStoredChart('{"sections":"no"}')).toBeNull();
  });

  it("search shows the line it found when it wasn't the title", () => {
    const song = { title: "Amazing Grace", body_text: AMAZING };
    expect(matchingLine(song, "wretch")).toBe("That saved a wretch like me");
    expect(matchingLine(song, "amazing")).toBeNull();
  });
});
