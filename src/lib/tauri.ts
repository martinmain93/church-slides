import { invoke } from "@/lib/invoke";
import type { ScriptureSettings } from "@/lib/scripture/settings";
import { withServiceItems } from "@/lib/serviceItems";
import type { NewSongInput, ReadingRow, SetlistDetail, SetlistRow, SongRow } from "@/core/library";

export type { NewSongInput, ReadingRow, SetlistDetail, SetlistItemRow, SetlistItemWithSong, SetlistRow, SongRow } from "@/core/library";



export type SortKey = "alphabetical" | "recently_edited" | "popular";

export interface AppSettings {
  default_tempo_wps: number;
  theme: string;
  /** When true, services started from the presentation view auto-record.
   * Off in release builds until someone agrees to the recording notice. */
  record_services: boolean;
  /** Someone agreed to the recording notice (Settings → Developer tools). */
  recording_acknowledged?: boolean;
  /** Recordings older than this many days are deleted at startup; 0 keeps. */
  recording_keep_days?: number;
  /** Send crash and error reports to the developers. Counts only once
   * `error_reports_asked`: off until someone says yes. */
  send_error_reports: boolean;
  /** Someone answered the error-report question (first run or Settings). */
  error_reports_asked?: boolean;
  /** Optional email or church name on error reports; empty = none. */
  report_contact: string;
  /** Share usage statistics, including the install count sent with update
   * checks: null/absent until asked, then the answer. */
  share_usage?: boolean | null;
  /** Act on spoken requests during a service: null/absent until chosen, which
   * leaves it to the engine (on when a hosted model's key is on file). */
  spoken_requests?: boolean | null;
  /** Bible/scripture display. Nested so this interface grows one field
   * however many display options that feature accumulates; absent in settings
   * blobs written before it existed, hence optional. */
  scripture?: ScriptureSettings;
}

export type ImportMode = "merge" | "replace";

export interface ImportSummary {
  schema_version: number;
  songs_imported: number;
  songs_replaced: number;
}






// --- Sidecar ---------------------------------------------------------------

export async function getSidecarPort(): Promise<number> {
  return invoke<number>("get_sidecar_port");
}

/**
 * The AI engine's state, from the shell that launched it (`EngineState` in
 * src-tauri/src/sidecar.rs). Everything but live AI works in every state, so
 * `failed` carries a reason to show the operator, not an error to throw.
 */
export type AiEngineStatus =
  | { state: "starting" }
  | { state: "ready"; port: number }
  | { state: "failed"; reason: string };

export async function getAiEngineStatus(): Promise<AiEngineStatus> {
  return invoke<AiEngineStatus>("get_ai_engine_status");
}

/** THIRD-PARTY-NOTICES.txt, bundled with the app: every open-source
 * component it ships and its licence (scripts/third_party_notices.py). */
export async function readThirdPartyNotices(): Promise<string> {
  return invoke<string>("read_third_party_notices");
}

// --- Songs -----------------------------------------------------------------

export async function listSongs(): Promise<SongRow[]> {
  return invoke<SongRow[]>("list_songs");
}

export async function listSongsSorted(sort: SortKey): Promise<SongRow[]> {
  return invoke<SongRow[]>("list_songs_sorted", { sort });
}

export async function searchSongs(query: string): Promise<SongRow[]> {
  return invoke<SongRow[]>("search_songs", { query });
}

export async function getSong(id: string): Promise<SongRow | null> {
  return invoke<SongRow | null>("get_song", { id });
}

export async function createSong(input: NewSongInput): Promise<string> {
  return invoke<string>("create_song", { input });
}

export async function updateSong(id: string, input: NewSongInput): Promise<void> {
  await invoke<void>("update_song", { id, input });
}

export async function deleteSong(id: string): Promise<void> {
  await invoke<void>("delete_song", { id });
}

// --- Settings --------------------------------------------------------------

export async function getSettings(): Promise<AppSettings> {
  return invoke<AppSettings>("get_settings");
}

export async function updateSettings(settings: AppSettings): Promise<AppSettings> {
  return invoke<AppSettings>("update_settings", { settings });
}

// --- Feedback and app identity ---------------------------------------------

/** What this install tells the stats Worker about itself (`feedback.rs`). */
export interface AppIdentity {
  install_id: string;
  version: string;
  os: string;
  os_version: string | null;
  arch: string;
}

export async function getAppIdentity(): Promise<AppIdentity> {
  return invoke<AppIdentity>("app_identity");
}

export type FeedbackKind = "problem" | "idea" | "praise" | "other";

/** Send feedback to the developers. Rejects with a message to show when it
 * didn't arrive (offline, say). */
/** A file the song import couldn't read — sent only if error reports are on. */
export async function reportUnreadableImport(extension: string, reason: string, signature: string): Promise<void> {
  await invoke<void>("report_unreadable_import", { extension, reason, signature });
}

export async function sendFeedback(args: {
  kind: FeedbackKind;
  message: string;
  contact: string;
  includeLog: boolean;
}): Promise<void> {
  return invoke<void>("send_feedback", args);
}

/** Open the app's website or an email to the developers (`open_link` only
 * opens those). */
export async function openLink(url: string): Promise<void> {
  return invoke<void>("open_link", { url });
}

// --- Library export / import ------------------------------------------------

export async function exportLibrary(): Promise<string> {
  return invoke<string>("export_library");
}

export async function importLibrary(json: string, mode: ImportMode): Promise<ImportSummary> {
  return invoke<ImportSummary>("import_library", { json, mode });
}

// --- Setlists --------------------------------------------------------------

// --- Libraries --------------------------------------------------------------

/** One library on this computer: its own, or an organization's. */
export interface LibraryInfo {
  id: string;
  name: string;
  kind: "computer" | "organization";
  open: boolean;
}

export async function listLibraries(): Promise<LibraryInfo[]> {
  return invoke<LibraryInfo[]>("list_libraries");
}

/** Switch libraries; every view reloads on the `library-changed` event. */
export async function openLibrary(id: string): Promise<void> {
  return invoke<void>("open_library", { id });
}

/** The open library's display look (JSON strings), or null if it has none. */
export async function getLibraryLook(): Promise<{ display: string | null; scripture: string | null } | null> {
  return invoke("get_library_look");
}

export async function setLibraryLook(display: string | null, scripture: string | null): Promise<void> {
  return invoke<void>("set_library_look", { display, scripture });
}

/** Whether to offer moving this computer's library into `orgId` (its owner,
 * songs here, the organization's library still empty). */
export async function canMoveLibrary(orgId: string): Promise<boolean> {
  return invoke<boolean>("can_move_library", { orgId });
}

/** Move this computer's library into an organization, and open it there. */
export async function moveLibraryInto(orgId: string): Promise<void> {
  return invoke<void>("move_library_into", { orgId });
}

export interface SongRevision {
  id: number;
  field: string;
  replacedAt: number;
  replacedBy: string | null;
  value?: string;
}

/** Earlier words of a song in an organization's library, newest first. */
export async function songRevisions(songId: string): Promise<SongRevision[]> {
  const r = await invoke<{ revisions: SongRevision[] }>("library_song_revisions", { songId });
  return r.revisions ?? [];
}

export async function songRevision(songId: string, revision: number): Promise<SongRevision> {
  const r = await invoke<{ revision: SongRevision }>("library_song_revision", { songId, revision });
  return r.revision;
}

/** Whose AI allowance this computer's AI uses, when signed in to an
 * organization; a Groq key of the person's own is used instead. */
export async function hostedAiStatus(): Promise<{ org_name: string | null; own_groq_key: boolean }> {
  return invoke("hosted_ai_status");
}

/** How the open organization library's sync stands. Shown only when
 * something's waiting or wrong. */
export interface LibrarySyncStatus {
  org_id: string | null;
  state: "off" | "syncing" | "ok" | "offline" | "signed_out" | "error";
  /** Changes made here that haven't gone up yet. */
  waiting: number;
  message: string | null;
}

export const LIBRARY_SYNC_EVENT = "library-sync";

export async function librarySyncStatus(): Promise<LibrarySyncStatus> {
  return invoke<LibrarySyncStatus>("library_sync_status");
}

/** Pull now: a change notice arrived. */
export async function librarySyncNow(): Promise<void> {
  return invoke<void>("library_sync_now");
}

/** A one-use websocket address for an organization's change notices. */
export async function librarySyncTicket(orgId: string): Promise<string> {
  return invoke<string>("library_sync_ticket", { orgId });
}

export async function listSetlists(): Promise<SetlistRow[]> {
  return invoke<SetlistRow[]>("list_setlists");
}

export async function getSetlist(id: string): Promise<SetlistDetail | null> {
  const detail = await invoke<SetlistDetail | null>("get_setlist", { id });
  return detail ? withServiceItems(detail) : null;
}

export async function createSetlist(name: string, serviceDate: string | null = null): Promise<string> {
  return invoke<string>("create_setlist", { name, serviceDate });
}

/** Change the day a service is for (`YYYY-MM-DD`, or null for none). */
export async function updateSetlistDate(id: string, serviceDate: string | null): Promise<void> {
  await invoke<void>("update_setlist_date", { id, serviceDate });
}

export async function updateSetlistName(id: string, name: string): Promise<void> {
  await invoke<void>("update_setlist_name", { id, name });
}

export async function deleteSetlist(id: string): Promise<void> {
  await invoke<void>("delete_setlist", { id });
}

export async function addSetlistItem(setlistId: string, songId: string): Promise<string> {
  return invoke<string>("add_setlist_item", { setlistId, songId });
}

/** Add a scripture or text item, after `afterItemId` (the selected item) or
 * at the end. */
export async function addServiceItem(
  setlistId: string,
  kind: "scripture" | "text",
  content: object,
  opts: { slidesOverride?: string | null; afterItemId?: string | null } = {},
): Promise<string> {
  return invoke<string>("add_service_item", {
    setlistId,
    kind,
    content: JSON.stringify(content),
    slidesOverride: opts.slidesOverride ?? null,
    afterItemId: opts.afterItemId ?? null,
  });
}

export async function setSetlistItemContent(
  setlistId: string,
  itemId: string,
  content: object,
): Promise<void> {
  await invoke<void>("set_setlist_item_content", { setlistId, itemId, content: JSON.stringify(content) });
}

/** A new setlist holding a copy of another's items ("Copy last service"). */
export async function duplicateSetlist(sourceId: string, name: string, serviceDate: string | null = null): Promise<string> {
  return invoke<string>("duplicate_setlist", { sourceId, name, serviceDate });
}

export async function listReadings(): Promise<ReadingRow[]> {
  return invoke<ReadingRow[]>("list_readings");
}

/** Save (or, by the same title, update) a reading in the library. */
export async function saveReading(title: string, bodyText: string): Promise<string> {
  return invoke<string>("save_reading", { title, bodyText });
}

export async function deleteReading(id: string): Promise<void> {
  await invoke<void>("delete_reading", { id });
}

export async function removeSetlistItem(setlistId: string, itemId: string): Promise<void> {
  await invoke<void>("remove_setlist_item", { setlistId, itemId });
}

export async function reorderSetlist(setlistId: string, itemIds: string[]): Promise<void> {
  await invoke<void>("reorder_setlist", { setlistId, itemIds });
}

/**
 * Set (or clear, with `null`) the per-setlist edited slide list for an item.
 * `slidesOverride` is the JSON produced by `serializeSlidesOverride`.
 */
export async function setSetlistItemSlides(
  setlistId: string,
  itemId: string,
  slidesOverride: string | null,
): Promise<void> {
  await invoke<void>("set_setlist_item_slides", { setlistId, itemId, slidesOverride });
}

/** Give an item its own display look in this setlist, or `null` for the default. */
/** Play a song in `key` for this setlist only; null goes back to its own key. */
export async function setSetlistItemKey(setlistId: string, itemId: string, key: string | null): Promise<void> {
  await invoke<void>("set_setlist_item_key", { setlistId, itemId, key });
}

export async function setSetlistItemLook(
  setlistId: string,
  itemId: string,
  look: string | null,
): Promise<void> {
  await invoke<void>("set_setlist_item_look", { setlistId, itemId, look });
}

export async function markSetlistUsed(id: string): Promise<void> {
  await invoke<void>("mark_setlist_used", { id });
}

/** A service: when a setlist was presented and the songs it held then. */
export interface ServiceRow {
  id: string;
  setlist_id: string;
  started_at: number;
  song_ids: string[];
}

/** The most recent services, newest first. */
export async function listRecentServices(limit: number): Promise<ServiceRow[]> {
  return invoke<ServiceRow[]>("list_recent_services", { limit });
}

// --- Chord charts -------------------------------------------------------------

export interface ChartRow {
  song_id: string;
  /** A `ChordChart` (src/lib/chords/chart.ts) as JSON. */
  chart_json: string;
  original_key: string | null;
  source_format: string | null;
  updated_at: number;
}

export interface ChartImportItem {
  /** Attach the chart to this library song instead of creating one. */
  existing_song_id: string | null;
  song: NewSongInput | null;
  chart_json: string;
  original_key: string | null;
  source_format: string | null;
  /** With `existing_song_id`: facts to fill in where the song has none. */
  fill_metadata?: { ccli: string | null; author: string | null; copyright: string | null } | null;
  /**
   * With `existing_song_id`: the song's lyrics with its sections named from
   * the chart (`relabel.ts`). Applied only if the song's body is still
   * `previous_body`, so an edit made meanwhile is never overwritten.
   */
  relabel?: { body_text: string; slide_order: string | null; previous_body: string } | null;
}

export interface ChartImportSummary {
  songs_created: number;
  charts_attached: number;
  song_ids: string[];
}

export async function getSongCharts(songIds: string[]): Promise<Record<string, ChartRow>> {
  return invoke<Record<string, ChartRow>>("get_song_charts", { songIds });
}

/** Song id → written key, for every song that has a chart. */
export async function listChartKeys(): Promise<Record<string, string | null>> {
  return invoke<Record<string, string | null>>("list_chart_keys");
}

/** Song id -> the chart's key, tempo (bpm) and time signature, for every song
 *  with a chord chart. Library search reads it for "fast songs", "in 6/8" and
 *  "fits with …". */
export interface ChartMetaRow {
  key: string | null;
  tempo: number | null;
  time: string | null;
  /** False for a chart that holds only a key, tempo or time signature. */
  has_chords: boolean;
}

export async function listChartMeta(): Promise<Record<string, ChartMetaRow>> {
  return invoke("list_chart_meta");
}

export async function deleteSongChart(songId: string): Promise<void> {
  await invoke<void>("delete_song_chart", { songId });
}

export async function importCharts(items: ChartImportItem[]): Promise<ChartImportSummary> {
  return invoke<ChartImportSummary>("import_charts", { items });
}

// --- Score-follower references --------------------------------------------------

/** What the score follower follows a song by: a lead sheet, later a recorded service. */
export type SongReferenceKind = "lead_sheet" | "recording";

export interface NewSongReference {
  song_id: string;
  kind: SongReferenceKind;
  source_name: string | null;
  /** The reference itself (for a lead sheet, the engine's parsed score). */
  data_json: string;
  /** Small display facts (bars, key, sections…) as JSON. */
  summary_json: string | null;
}

export interface SongReferenceSummary {
  id: string;
  song_id: string;
  kind: SongReferenceKind;
  source_name: string | null;
  summary_json: string;
  created_at: number;
  updated_at: number;
}

/** One song's references, or every song's when `songId` is omitted. */
export async function listSongReferences(songId?: string): Promise<SongReferenceSummary[]> {
  return invoke<SongReferenceSummary[]>("list_song_references", { songId: songId ?? null });
}

/** Saves a reference. A song keeps one lead sheet: a new one replaces it. */
export async function saveSongReference(reference: NewSongReference): Promise<SongReferenceSummary> {
  return invoke<SongReferenceSummary>("save_song_reference", { reference });
}

export async function deleteSongReference(id: string): Promise<void> {
  await invoke<void>("delete_song_reference", { id });
}

// --- Band screens -------------------------------------------------------------

export interface BandInfo {
  port: number;
  /** Addresses devices can reach this computer on, best guess first. */
  urls: string[];
  /** Unlocks the leader controls. */
  pin: string;
  clients: number;
}

export async function bandStart(): Promise<BandInfo> {
  return invoke<BandInfo>("band_start");
}

export async function bandInfo(): Promise<BandInfo | null> {
  return invoke<BandInfo | null>("band_info");
}

export async function bandPublish(kind: "setlist" | "position" | "hosted", payload: unknown): Promise<void> {
  await invoke<void>("band_publish", { kind, payload });
}

/** Open a band screen window on this computer (for a spare monitor); starts band screens if needed. */
export async function openBandWindow(): Promise<string> {
  return invoke<string>("open_band_window");
}

/** Close every band screen window on this computer; resolves to how many closed. */
export async function closeBandWindows(): Promise<number> {
  return invoke<number>("close_band_windows");
}

export async function countBandWindows(): Promise<number> {
  return invoke<number>("count_band_windows");
}

export async function bandClear(): Promise<void> {
  await invoke<void>("band_clear");
}

/** Hold (or release) the "don't sleep the display" assertion while presenting. */
export async function setKeepAwake(on: boolean): Promise<void> {
  await invoke<void>("set_keep_awake", { on });
}

// --- Operator-to-operator setlist transfer (src-tauri/src/peer_sync.rs) ---------

export const PEERS_EVENT = "peer-sync://peers";
export const PEER_OFFER_EVENT = "peer-sync://offer";
export const PEER_ACCESS_EVENT = "peer-sync://access-request";

export interface PeerHello {
  id: string;
  name: string;
  protocol: number;
  app_version: string;
}

export interface Peer {
  id: string;
  name: string;
  base_url: string;
  /** Added by address rather than found on the network. */
  manual: boolean;
}

export interface PeerSyncInfo {
  id: string;
  name: string;
  port: number | null;
  /** What to type on the other computer if it can't find this one. */
  addresses: string[];
  discovery_error: string | null;
  /** The end of the Wi-Fi router's hardware address ("D612"): the same on
   * every computer on this network. Null when there's no router. */
  network_code: string | null;
}

export interface BundleSong {
  ref_id: string;
  title: string;
  body_text: string;
  slide_order: string | null;
  ccli: string | null;
  author: string | null;
  copyright: string | null;
  default_tempo_wps: number | null;
  chart: { chart_json: string; original_key: string | null; source_format: string | null } | null;
  scriptures: unknown[];
}

export interface SetlistBundle {
  protocol: number;
  setlist_name: string;
  songs: BundleSong[];
  /** `song_ref` is empty for a scripture or text item, which carries its
   * own `content` (older senders leave `kind` out: songs only). */
  items: { song_ref: string; slides_override: string | null; kind?: string; content?: string | null }[];
  /** Every song in the sender's library and no setlist (older senders leave it out). */
  library?: boolean;
}

export type SongDecision =
  | { kind: "new" }
  | { kind: "use_existing"; song_id: string }
  | { kind: "replace_existing"; song_id: string }
  | { kind: "keep_both" };

export interface SyncApplySummary {
  /** Empty for a whole library, which makes no setlist. */
  setlist_id: string;
  setlist_name: string;
  songs_created: number;
  songs_reused: number;
  songs_replaced: number;
  charts_added: number;
  backup_path: string | null;
}

export interface RemoteSetlist {
  id: string;
  name: string;
  song_count: number;
  last_used_at: number | null;
  created_at: number;
}

export type PeerRequestStatus = "pending" | "accepted" | "declined" | "expired";

export interface PeerOfferEvent {
  offer_id: string;
  from: PeerHello;
  setlist_name: string;
  song_count: number;
  /** Their whole library rather than one setlist. */
  library?: boolean;
}

export interface PeerAccessEvent {
  request_id: string;
  from: PeerHello;
}

export const peerSyncInfo = () => invoke<PeerSyncInfo>("peer_sync_info");
export const listPeers = () => invoke<Peer[]>("peer_list_peers");
export const addPeerByAddress = (address: string) => invoke<Peer>("peer_add_manual", { address });
export const sendSetlistToPeer = (peerId: string, setlistId: string) =>
  invoke<string>("peer_send_setlist", { peerId, setlistId });
export const sendLibraryToPeer = (peerId: string) => invoke<string>("peer_send_library", { peerId });
export const peerOfferStatus = (peerId: string, offerId: string) =>
  invoke<PeerRequestStatus>("peer_offer_status", { peerId, offerId });
export const requestPeerAccess = (peerId: string) => invoke<string>("peer_request_access", { peerId });
export const peerHasAccess = (peerId: string) => invoke<boolean>("peer_has_access", { peerId });
export const peerAccessStatus = (peerId: string, requestId: string) =>
  invoke<PeerRequestStatus>("peer_access_status", { peerId, requestId });
export const listPeerSetlists = (peerId: string) => invoke<RemoteSetlist[]>("peer_remote_setlists", { peerId });
export const fetchPeerSetlist = (peerId: string, setlistId: string) =>
  invoke<SetlistBundle>("peer_fetch_setlist", { peerId, setlistId });
export const fetchPeerLibrary = (peerId: string) => invoke<SetlistBundle>("peer_fetch_library", { peerId });
export const incomingPeerOffer = (offerId: string) =>
  invoke<{ from: PeerHello; bundle: SetlistBundle }>("peer_incoming_offer", { offerId });
export const respondToPeerOffer = (offerId: string, accepted: boolean) =>
  invoke<void>("peer_respond_offer", { offerId, accepted });
export const respondToPeerAccess = (requestId: string, allow: boolean) =>
  invoke<void>("peer_respond_access", { requestId, allow });
export const applySetlistBundle = (bundle: SetlistBundle, decisions: Record<string, SongDecision>, fromName: string) =>
  invoke<SyncApplySummary>("peer_apply_bundle", { bundle, decisions, fromName });

// --- Online band relay (the church's own Cloudflare account) -----------------

export interface CloudStatus {
  configured: boolean;
  worker_url: string | null;
  account_name: string | null;
  /** The app carries a newer relay than the deployed one. */
  needs_update: boolean;
  /** This build can sign in with Cloudflare in the browser. */
  oauth_available: boolean;
  /** This build includes the relay (dev builds may not). */
  relay_built: boolean;
}

export type CloudSetupOutcome =
  | { outcome: "done"; status: CloudStatus }
  | { outcome: "choose_account"; accounts: { id: string; name: string }[] };

export interface CloudRoom {
  base_url: string;
  room: string;
  key: string;
  host_socket: string;
}

export const cloudRelayStatus = () => invoke<CloudStatus>("cloud_relay_status");
export const cloudRelaySetupToken = (token: string, accountId?: string) =>
  invoke<CloudSetupOutcome>("cloud_relay_setup_token", { token, accountId: accountId ?? null });
export const cloudRelaySetupOauth = (accountId?: string) =>
  invoke<CloudSetupOutcome>("cloud_relay_setup_oauth", { accountId: accountId ?? null });
export const cloudRelayRedeploy = () => invoke<CloudStatus>("cloud_relay_redeploy");
export const cloudRelayHealth = () => invoke<boolean>("cloud_relay_health");
export const cloudRelayForget = () => invoke<void>("cloud_relay_forget");
export const cloudRelayNewRoom = (pin: string) => invoke<CloudRoom>("cloud_relay_new_room", { pin });
export const cloudRelayNewLinks = () => invoke<void>("cloud_relay_new_links");
export const cloudRelayOpenPage = (url: string) => invoke<void>("cloud_relay_open_page", { url });
export const bandLibraryIndex = () => invoke<import("@/lib/band/librarySearch").LibrarySong[]>("band_library_index");
