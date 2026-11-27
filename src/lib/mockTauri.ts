/**
 * In-memory mock of the Rust Tauri commands, used when the React app runs
 * in a regular browser (e.g. `pnpm dev` for UI iteration) instead of inside
 * the Tauri shell.
 *
 * State persists to localStorage so reloads don't lose your mock library.
 * The behavior intentionally mirrors `src-tauri/src/db.rs` closely:
 * alphabetical sort, FTS-style search, ULID-ish IDs, ms-precision timestamps.
 *
 * NOT used in production. The dispatcher in `src/lib/invoke.ts` only routes
 * here when `__TAURI_INTERNALS__` is missing.
 */

import type {
  AiEngineStatus,
  AppSettings,
  ChartImportItem,
  ChartImportSummary,
  ChartRow,
  ImportMode,
  ImportSummary,
  NewSongInput,
  NewSongReference,
  ReadingRow,
  SetlistDetail,
  SetlistItemWithSong,
  SetlistRow,
  SongReferenceSummary,
  SongRow,
  SortKey,
} from "@/lib/tauri";

const STORAGE_KEY = "auto_presenter_mock_library_v1";

const SEED_SONG: SongRow = {
  id: "song_amazing_grace",
  title: "Amazing Grace",
  body_text:
    "[verse 1]\nAmazing grace how sweet the sound\nThat saved a wretch like me\n\n[verse 2]\n'Twas grace that taught my heart to fear\nAnd grace my fears relieved",
  ccli: null,
  author: "John Newton",
  copyright: null,
  default_tempo_wps: null,
  slide_order: null,
  created_at: 0,
  updated_at: 0,
};

interface MockSetlist {
  id: string;
  name: string;
  created_at: number;
  last_used_at: number | null;
  /** Ordered list of item IDs; we look up song refs via a parallel map. */
  item_ids: string[];
}

interface MockSetlistItem {
  id: string;
  setlist_id: string;
  /** Older stored state has songs only. */
  kind?: "song" | "scripture" | "text";
  song_id: string | null;
  slides_override?: string | null;
  content?: string | null;
  look?: string | null;
  song_key?: string | null;
}

interface MockState {
  songs: Record<string, SongRow>;
  settings: AppSettings;
  setlists: Record<string, MockSetlist>;
  setlist_items: Record<string, MockSetlistItem>;
  readings?: Record<string, ReadingRow>;
  /** song id -> chord chart, mirroring the `song_charts` table. */
  charts?: Record<string, ChartRow>;
  /** Score-follower references, mirroring the `song_references` table (payloads included). */
  references?: (SongReferenceSummary & { data_json: string })[];
}

const DEFAULT_SETTINGS: AppSettings = {
  default_tempo_wps: 2.0,
  theme: "dark",
  record_services: true,
  recording_acknowledged: false,
  recording_keep_days: 0,
  send_error_reports: false,
  error_reports_asked: false,
  report_contact: "",
  share_usage: null,
};

function load(): MockState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<MockState>;
      if (parsed && typeof parsed === "object" && parsed.songs) {
        return {
          songs: parsed.songs,
          settings: { ...DEFAULT_SETTINGS, ...(parsed.settings ?? {}) },
          setlists: parsed.setlists ?? {},
          setlist_items: parsed.setlist_items ?? {},
          readings: parsed.readings ?? {},
          charts: parsed.charts ?? {},
          references: parsed.references ?? [],
        };
      }
    }
  } catch {
    // fall through to seeded state
  }
  return {
    songs: { [SEED_SONG.id]: SEED_SONG },
    settings: { ...DEFAULT_SETTINGS },
    setlists: {},
    setlist_items: {},
  };
}

function save(state: MockState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // out of quota / private mode — silently ignore for dev
  }
}

let state: MockState | null = null;
function get(): MockState {
  if (state === null) state = load();
  return state;
}

function persist(): void {
  if (state) save(state);
}

function newId(): string {
  // 26-char base32-ish stamp; not a real ULID but unique enough for dev.
  const time = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 10);
  return `song_${time}${rand}`.toLowerCase();
}

function nowMs(): number {
  return Date.now();
}

function alphabetical(a: SongRow, b: SongRow): number {
  return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
}

function sorted(rows: SongRow[], sort: SortKey): SongRow[] {
  if (sort === "recently_edited") {
    return [...rows].sort((a, b) => {
      if (b.updated_at !== a.updated_at) return b.updated_at - a.updated_at;
      return alphabetical(a, b);
    });
  }
  // No setlists in browser dev mode, so popular falls back to alphabetical.
  return [...rows].sort(alphabetical);
}

// Account sign-in, faked: "signs in" instantly as a member of one organization.
const signedOutAccount = {
  signedIn: false,
  userId: null,
  email: null,
  displayName: null,
  memberships: [],
  server: "https://api.churchslides.app",
  serverIsDefault: true,
};
type MockMember = { userId: string; displayName: string | null; email: string; role: string; joinedAt: number };
const mockOrgsStart = (): Record<string, MockMember[]> => ({
  o1: [
    { userId: "u-pat", displayName: "Pat", email: "pat@example.org", role: "owner", joinedAt: 0 },
    { userId: "u-ada", displayName: "Ada", email: "ada@example.org", role: "operator", joinedAt: 0 },
  ],
  o2: [{ userId: "u-sam", displayName: "Sam", email: "sam@example.org", role: "owner", joinedAt: 0 }],
});
let mockOrgs = mockOrgsStart();
let mockAccount: typeof signedOutAccount | {
  signedIn: boolean; userId: string; email: string; displayName: string;
  memberships: { orgId: string; orgName: string; role: string }[];
  server: string; serverIsDefault: boolean;
} = signedOutAccount;

export const mockTauri = {
  async account_status() {
    return mockAccount;
  },
  async account_refresh() {
    return mockAccount;
  },
  async account_sign_in(args?: { invite?: string | null }) {
    mockOrgs = mockOrgsStart();
    const memberships = [{ orgId: "o1", orgName: "Grace Chapel", role: "operator" }];
    if (args?.invite) memberships.push({ orgId: "o2", orgName: "Hope Church", role: "member" });
    mockAccount = {
      signedIn: true,
      userId: "u-ada",
      email: "ada@example.org",
      displayName: "Ada",
      memberships,
      server: "https://api.churchslides.app",
      serverIsDefault: true,
    };
    return mockAccount;
  },
  async account_sign_out() {
    mockAccount = signedOutAccount;
    return mockAccount;
  },
  async account_sign_out_everywhere() {
    mockAccount = signedOutAccount;
    return mockAccount;
  },
  /** The organization API, enough to click through Settings → Account. */
  async account_api({ method, path, body }: { method: string; path: string; body: Record<string, unknown> | null }) {
    if (!mockAccount.signedIn) throw new Error("This computer was signed out. Sign in again to continue.");
    const me = mockAccount as Exclude<typeof mockAccount, typeof signedOutAccount>;
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    if (method === "POST" && path === "/orgs") {
      const id = `o${Date.now()}`;
      mockOrgs[id] = [{ userId: me.userId, displayName: me.displayName, email: me.email, role: "owner", joinedAt: Date.now() }];
      me.memberships = [...me.memberships, { orgId: id, orgName: String(body?.name), role: "owner" }];
      return { org: { id, name: body?.name }, role: "owner" };
    }
    if (method === "POST" && path === "/invites/accept") {
      if (!String(body?.invite).includes("csi_")) throw new Error("This invite has expired or been withdrawn. Ask for a new one.");
      const already = me.memberships.find((m) => m.orgId === "o2");
      if (!already) me.memberships = [...me.memberships, { orgId: "o2", orgName: "Hope Church", role: "member" }];
      return { membership: { orgId: "o2", orgName: "Hope Church", role: already?.role ?? "member" }, joined: !already };
    }
    const [, orgId, what, id] = parts;
    const members = mockOrgs[orgId ?? ""];
    if (!members) throw new Error("No such organization.");
    if (what === "members" && method === "GET") {
      const mine = me.memberships.find((m) => m.orgId === orgId)?.role;
      const manage = mine === "owner" || mine === "admin";
      return { members: members.map((m) => ({ ...m, email: manage || m.userId === me.userId ? m.email : undefined })) };
    }
    if (what === "members" && method === "PATCH") {
      const m = members.find((x) => x.userId === id);
      if (m) m.role = String(body?.role);
      return null;
    }
    if (what === "members" && method === "DELETE") {
      mockOrgs[orgId!] = members.filter((x) => x.userId !== id);
      if (id === me.userId) me.memberships = me.memberships.filter((m) => m.orgId !== orgId);
      return null;
    }
    if (what === "invites" && method === "POST") {
      return { url: "https://api.churchslides.app/join/csi_mockInviteCode0123456789", code: "csi_mockInviteCode0123456789" };
    }
    if (what === "band-devices" && method === "POST") {
      const token = `csb_mock${body?.canLead ? "Leader" : "Band"}${Date.now()}`;
      return { device: { id: `d${Date.now()}`, name: body?.name, canLead: Boolean(body?.canLead) }, token, url: `https://churchslides.app/live/#s=https%3A%2F%2Fapi.churchslides.app&t=${token}` };
    }
    if (what === "live" && id === "ticket" && method === "POST") {
      // No hosted room in the browser preview: the socket fails and retries.
      return { url: "ws://127.0.0.1:9/live/host?ticket=cst_mock" };
    }
    throw new Error(`mock: ${method} ${path} isn't mocked`);
  },
  async get_sidecar_port(): Promise<number> {
    // No sidecar in browser dev mode — surface as connection failure so
    // the AI status badge correctly shows "offline". To exercise engine
    // features from the browser, start one by hand
    // (`uv run --project sidecar python -m auto_presenter --port 8791`) and
    // set localStorage "mock:sidecarPort" to its port.
    let port = NaN;
    try {
      port = Number(localStorage.getItem("mock:sidecarPort"));
    } catch {
      /* storage blocked */
    }
    if (port > 0) return port;
    throw new Error("sidecar unavailable in browser dev mode");
  },

  async get_ai_engine_status(): Promise<AiEngineStatus> {
    return {
      state: "failed",
      reason: "sidecar unavailable in browser dev mode — live AI needs the desktop app.",
    };
  },

  async list_songs(): Promise<SongRow[]> {
    return Object.values(get().songs).sort(alphabetical);
  },

  async list_songs_sorted(args: { sort: SortKey }): Promise<SongRow[]> {
    return sorted(Object.values(get().songs), args.sort);
  },

  async search_songs(args: { query: string }): Promise<SongRow[]> {
    const q = args.query.trim().toLowerCase();
    if (!q) return Object.values(get().songs).sort(alphabetical);
    const matches = Object.values(get().songs).filter((s) => {
      return (
        s.title.toLowerCase().includes(q) ||
        s.body_text.toLowerCase().includes(q)
      );
    });
    return matches.sort(alphabetical);
  },

  async get_song(args: { id: string }): Promise<SongRow | null> {
    return get().songs[args.id] ?? null;
  },

  async create_song(args: { input: NewSongInput }): Promise<string> {
    const id = newId();
    const now = nowMs();
    const row: SongRow = {
      id,
      title: args.input.title,
      body_text: args.input.body_text,
      ccli: args.input.ccli ?? null,
      author: args.input.author ?? null,
      copyright: args.input.copyright ?? null,
      default_tempo_wps: args.input.default_tempo_wps ?? null,
      slide_order: args.input.slide_order ?? null,
      created_at: now,
      updated_at: now,
    };
    get().songs[id] = row;
    persist();
    return id;
  },

  async update_song(args: { id: string; input: NewSongInput }): Promise<void> {
    const existing = get().songs[args.id];
    if (!existing) throw new Error(`no song with id ${args.id}`);
    get().songs[args.id] = {
      ...existing,
      title: args.input.title,
      body_text: args.input.body_text,
      ccli: args.input.ccli ?? null,
      author: args.input.author ?? null,
      copyright: args.input.copyright ?? null,
      default_tempo_wps: args.input.default_tempo_wps ?? null,
      slide_order: args.input.slide_order ?? null,
      updated_at: nowMs(),
    };
    persist();
  },

  async delete_song(args: { id: string }): Promise<void> {
    const st = get();
    delete st.songs[args.id];
    // ON DELETE CASCADE, as in the real tables.
    delete st.charts?.[args.id];
    if (st.references) st.references = st.references.filter((r) => r.song_id !== args.id);
    persist();
  },

  async get_settings(): Promise<AppSettings> {
    return { ...get().settings };
  },

  async update_settings(args: { settings: AppSettings }): Promise<AppSettings> {
    get().settings = { ...args.settings };
    persist();
    return { ...get().settings };
  },

  async read_third_party_notices(): Promise<string> {
    return "Church Slides: third-party notices\n\n(Mock: the real file is generated by scripts/third_party_notices.py.)\n";
  },

  async app_identity() {
    return { install_id: "01JA2B3C4D5E6F7G8H9JKMNPQR", version: "0.0.0-dev", os: "macos", os_version: null, arch: "aarch64" };
  },

  async open_link(args: { url: string }): Promise<void> {
    window.open(args.url, "_blank", "noopener");
  },

  async send_feedback(): Promise<void> {
    // Nothing to send to in the browser; the dialog's success path still runs.
  },

  async export_library(): Promise<string> {
    const blob = {
      schema_version: 1,
      exported_at: nowMs(),
      songs: Object.values(get().songs).sort(alphabetical),
    };
    return JSON.stringify(blob, null, 2);
  },

  async import_library(args: { json: string; mode: ImportMode }): Promise<ImportSummary> {
    interface Blob {
      schema_version: number;
      exported_at: number;
      songs: SongRow[];
    }
    let parsed: Blob;
    try {
      parsed = JSON.parse(args.json) as Blob;
    } catch (err) {
      throw new Error(`parsing library import: ${err instanceof Error ? err.message : err}`, { cause: err });
    }
    if (parsed.schema_version > 1) {
      throw new Error(
        `library was exported with schema_version=${parsed.schema_version} but this build supports up to 1`,
      );
    }
    if (args.mode === "replace") {
      get().songs = {};
    }
    let imported = 0;
    let replaced = 0;
    for (const song of parsed.songs) {
      if (get().songs[song.id]) replaced += 1;
      else imported += 1;
      get().songs[song.id] = { ...song };
    }
    persist();
    return {
      schema_version: parsed.schema_version,
      songs_imported: imported,
      songs_replaced: replaced,
    };
  },

  // --- Libraries ----------------------------------------------------------

  async get_library_look() {
    return null;
  },
  async set_library_look() {},
  async can_move_library() {
    return false;
  },
  async hosted_ai_status() {
    return { org_name: null, own_groq_key: false, speech: false };
  },
  async library_sync_status() {
    return { org_id: null, state: "off", waiting: 0, message: null };
  },
  async library_sync_now() {},
  async list_libraries() {
    return [{ id: "this-computer", name: "This computer", kind: "computer", open: true }];
  },

  async open_library() {},

  // --- Setlists -----------------------------------------------------------

  async list_setlists(): Promise<SetlistRow[]> {
    // Named like `setlists::list_setlists`: songs by title, passages and
    // text items by their reference or title.
    const label = (it: MockSetlistItem): string | null => {
      if (it.song_id) return get().songs[it.song_id]?.title ?? null;
      try {
        const c = JSON.parse(it.content ?? "{}") as { reference?: string; title?: string };
        return c.reference ?? c.title ?? null;
      } catch {
        return null;
      }
    };
    const out: SetlistRow[] = Object.values(get().setlists).map((sl) => ({
      ...toRow(sl),
      song_titles: sl.item_ids
        .map((id) => get().setlist_items[id])
        .map((it) => (it ? label(it) : null))
        .filter((t): t is string => t !== null),
      song_count: sl.item_ids.filter((id) => get().setlist_items[id]?.song_id).length,
    }));
    return out.sort((a, b) => {
      const recency = (b.last_used_at ?? 0) - (a.last_used_at ?? 0);
      if (recency !== 0) return recency;
      return b.created_at - a.created_at;
    });
  },

  async get_setlist(args: { id: string }): Promise<SetlistDetail | null> {
    const sl = get().setlists[args.id];
    if (!sl) return null;
    const items: SetlistItemWithSong[] = sl.item_ids.map((itemId, idx) => {
      const item = get().setlist_items[itemId]!;
      return {
        id: item.id,
        setlist_id: item.setlist_id,
        kind: item.kind ?? "song",
        song_id: item.song_id,
        position: idx,
        slides_override: item.slides_override ?? null,
        content: item.content ?? null,
        look: item.look ?? null,
        song_key: item.song_key ?? null,
        song: item.song_id ? (get().songs[item.song_id] ?? null) : null,
      };
    });
    return { ...toRow(sl), items };
  },

  async create_setlist(args: { name: string }): Promise<string> {
    const id = `setlist_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    get().setlists[id] = {
      id,
      name: args.name,
      created_at: nowMs(),
      last_used_at: null,
      item_ids: [],
    };
    persist();
    return id;
  },

  async update_setlist_name(args: { id: string; name: string }): Promise<void> {
    const sl = get().setlists[args.id];
    if (!sl) throw new Error(`no setlist with id ${args.id}`);
    sl.name = args.name;
    persist();
  },

  async delete_setlist(args: { id: string }): Promise<void> {
    const sl = get().setlists[args.id];
    if (!sl) return;
    for (const itemId of sl.item_ids) delete get().setlist_items[itemId];
    delete get().setlists[args.id];
    persist();
  },

  async add_setlist_item(args: {
    setlistId: string;
    songId: string;
  }): Promise<string> {
    const sl = get().setlists[args.setlistId];
    if (!sl) throw new Error(`no setlist with id ${args.setlistId}`);
    if (!get().songs[args.songId]) {
      throw new Error(`no song with id ${args.songId}`);
    }
    const id = `item_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    get().setlist_items[id] = {
      id,
      setlist_id: args.setlistId,
      song_id: args.songId,
    };
    sl.item_ids.push(id);
    persist();
    return id;
  },

  async add_service_item(args: {
    setlistId: string;
    kind: "scripture" | "text";
    content: string;
    slidesOverride: string | null;
    afterItemId: string | null;
  }): Promise<string> {
    const sl = get().setlists[args.setlistId];
    if (!sl) throw new Error(`no setlist with id ${args.setlistId}`);
    const id = `item_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    get().setlist_items[id] = {
      id,
      setlist_id: args.setlistId,
      kind: args.kind,
      song_id: null,
      slides_override: args.slidesOverride,
      content: args.content,
    };
    const after = args.afterItemId ? sl.item_ids.indexOf(args.afterItemId) : -1;
    if (after >= 0) sl.item_ids.splice(after + 1, 0, id);
    else sl.item_ids.push(id);
    persist();
    return id;
  },

  async set_setlist_item_content(args: {
    setlistId: string;
    itemId: string;
    content: string;
  }): Promise<void> {
    const item = get().setlist_items[args.itemId];
    if (!item || item.setlist_id !== args.setlistId || (item.kind ?? "song") === "song") {
      throw new Error(`no scripture or text item ${args.itemId} in setlist ${args.setlistId}`);
    }
    item.content = args.content;
    persist();
  },

  async duplicate_setlist(args: { sourceId: string; name: string }): Promise<string> {
    const src = get().setlists[args.sourceId];
    if (!src) throw new Error(`no setlist with id ${args.sourceId}`);
    const id = `setlist_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    const itemIds = src.item_ids.map((itemId) => {
      const copyId = `item_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      get().setlist_items[copyId] = { ...get().setlist_items[itemId]!, id: copyId, setlist_id: id };
      return copyId;
    });
    get().setlists[id] = { id, name: args.name, created_at: nowMs(), last_used_at: null, item_ids: itemIds };
    persist();
    return id;
  },

  async list_readings(): Promise<ReadingRow[]> {
    return Object.values(get().readings ?? {}).sort((a, b) => a.title.localeCompare(b.title));
  },

  async save_reading(args: { title: string; bodyText: string }): Promise<string> {
    const title = args.title.trim();
    if (!title) throw new Error("a reading needs a title");
    const readings = (get().readings ??= {});
    const existing = Object.values(readings).find((r) => r.title.toLowerCase() === title.toLowerCase());
    const now = nowMs();
    const id = existing?.id ?? `reading_${now.toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    readings[id] = { id, title, body_text: args.bodyText, created_at: existing?.created_at ?? now, updated_at: now };
    persist();
    return id;
  },

  async delete_reading(args: { id: string }): Promise<void> {
    delete get().readings?.[args.id];
    persist();
  },

  async remove_setlist_item(args: {
    setlistId: string;
    itemId: string;
  }): Promise<void> {
    const sl = get().setlists[args.setlistId];
    if (!sl) throw new Error(`no setlist with id ${args.setlistId}`);
    const idx = sl.item_ids.indexOf(args.itemId);
    if (idx < 0) {
      throw new Error(`no item with id ${args.itemId} in setlist ${args.setlistId}`);
    }
    sl.item_ids.splice(idx, 1);
    delete get().setlist_items[args.itemId];
    persist();
  },

  async set_setlist_item_slides(args: {
    setlistId: string;
    itemId: string;
    slidesOverride: string | null;
  }): Promise<void> {
    const item = get().setlist_items[args.itemId];
    if (!item || item.setlist_id !== args.setlistId) {
      throw new Error(`no item with id ${args.itemId} in setlist ${args.setlistId}`);
    }
    item.slides_override = args.slidesOverride;
    persist();
  },

  async set_setlist_item_key(args: { setlistId: string; itemId: string; key: string | null }): Promise<void> {
    const item = get().setlist_items[args.itemId];
    if (!item || item.setlist_id !== args.setlistId || (item.kind ?? "song") !== "song") {
      throw new Error(`no song item with id ${args.itemId} in setlist ${args.setlistId}`);
    }
    item.song_key = args.key;
    persist();
  },

  async set_setlist_item_look(args: {
    setlistId: string;
    itemId: string;
    look: string | null;
  }): Promise<void> {
    const item = get().setlist_items[args.itemId];
    if (!item || item.setlist_id !== args.setlistId) {
      throw new Error(`no item with id ${args.itemId} in setlist ${args.setlistId}`);
    }
    item.look = args.look;
    persist();
  },

  async reorder_setlist(args: {
    setlistId: string;
    itemIds: string[];
  }): Promise<void> {
    const sl = get().setlists[args.setlistId];
    if (!sl) throw new Error(`no setlist with id ${args.setlistId}`);
    for (const id of args.itemIds) {
      if (get().setlist_items[id]?.setlist_id !== args.setlistId) {
        throw new Error(`item ${id} does not belong to setlist ${args.setlistId}`);
      }
    }
    // Items not present in `itemIds` keep their order at the tail.
    const tail = sl.item_ids.filter((id) => !args.itemIds.includes(id));
    sl.item_ids = [...args.itemIds, ...tail];
    persist();
  },

  async mark_setlist_used(args: { id: string }): Promise<void> {
    const sl = get().setlists[args.id];
    if (!sl) return;
    sl.last_used_at = nowMs();
    persist();
  },

  // Browser dev mode keeps no service log: "not played lately" lets everything through.
  async list_recent_services(): Promise<[]> {
    return [];
  },

  async get_song_charts(args: { songIds: string[] }): Promise<Record<string, ChartRow>> {
    const charts = get().charts ?? {};
    const out: Record<string, ChartRow> = {};
    for (const id of args.songIds) if (charts[id]) out[id] = charts[id]!;
    return out;
  },

  async list_chart_keys(): Promise<Record<string, string | null>> {
    const out: Record<string, string | null> = {};
    for (const [id, c] of Object.entries(get().charts ?? {})) out[id] = c.original_key;
    return out;
  },

  async list_chart_meta(): Promise<
    Record<string, { key: string | null; tempo: number | null; time: string | null; has_chords: boolean }>
  > {
    const out: Record<string, { key: string | null; tempo: number | null; time: string | null; has_chords: boolean }> = {};
    for (const [id, c] of Object.entries(get().charts ?? {})) {
      let chart: { tempo?: unknown; time?: unknown } = {};
      try {
        chart = JSON.parse(c.chart_json) as typeof chart;
      } catch {
        // an unreadable chart has no tempo or time
      }
      out[id] = {
        key: c.original_key,
        tempo: typeof chart.tempo === "number" ? chart.tempo : null,
        time: typeof chart.time === "string" ? chart.time : null,
        has_chords: c.chart_json.includes('"chord":'),
      };
    }
    return out;
  },

  async delete_song_chart(args: { songId: string }): Promise<void> {
    delete get().charts?.[args.songId];
    persist();
  },

  async import_charts(args: { items: ChartImportItem[] }): Promise<ChartImportSummary> {
    const st = get();
    st.charts ??= {};
    const summary: ChartImportSummary = { songs_created: 0, charts_attached: 0, song_ids: [] };
    for (const item of args.items) {
      let id = item.existing_song_id;
      if (id) {
        const existing = st.songs[id];
        if (!existing) throw new Error(`no song with id ${id}`);
        const fill = item.fill_metadata;
        if (fill) {
          existing.ccli ||= fill.ccli;
          existing.author ||= fill.author;
          existing.copyright ||= fill.copyright;
        }
        summary.charts_attached += 1;
      } else {
        id = await mockTauri.create_song({ input: item.song! });
        summary.songs_created += 1;
      }
      st.charts[id] = {
        song_id: id,
        chart_json: item.chart_json,
        original_key: item.original_key,
        source_format: item.source_format,
        updated_at: nowMs(),
      };
      summary.song_ids.push(id);
    }
    persist();
    return summary;
  },

  async list_song_references(args: { songId: string | null }): Promise<SongReferenceSummary[]> {
    return (get().references ?? [])
      .filter((r) => args.songId === null || r.song_id === args.songId)
      .map(({ data_json: _data, ...summary }) => summary);
  },

  async save_song_reference(args: { reference: NewSongReference }): Promise<SongReferenceSummary> {
    const st = get();
    const r = args.reference;
    if (!st.songs[r.song_id]) throw new Error(`no song with id ${r.song_id}`);
    st.references ??= [];
    const now = nowMs();
    let created = now;
    if (r.kind === "lead_sheet") {
      // One lead sheet per song, as in the real table.
      const old = st.references.find((x) => x.song_id === r.song_id && x.kind === "lead_sheet");
      if (old) created = old.created_at;
      st.references = st.references.filter((x) => !(x.song_id === r.song_id && x.kind === "lead_sheet"));
    }
    const row = {
      id: `ref_${Math.random().toString(36).slice(2, 12)}`,
      song_id: r.song_id,
      kind: r.kind,
      source_name: r.source_name,
      summary_json: r.summary_json ?? "{}",
      created_at: created,
      updated_at: now,
      data_json: r.data_json,
    };
    st.references.push(row);
    persist();
    const { data_json: _data, ...summary } = row;
    return summary;
  },

  async delete_song_reference(args: { id: string }): Promise<void> {
    const st = get();
    st.references = (st.references ?? []).filter((r) => r.id !== args.id);
    persist();
  },

  // Band screens need the desktop app's network server.
  async band_info(): Promise<null> {
    return null;
  },
  async band_start(): Promise<never> {
    throw new Error("Band screens need the desktop app");
  },
  async band_publish(): Promise<void> {},
  async band_clear(): Promise<void> {},
};

function toRow(sl: MockSetlist): SetlistRow {
  return {
    id: sl.id,
    name: sl.name,
    created_at: sl.created_at,
    last_used_at: sl.last_used_at,
  };
}
