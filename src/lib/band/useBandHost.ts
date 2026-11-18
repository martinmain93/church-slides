/**
 * Operator side of band screens.
 *
 * Publishes the running service to the band server and applies what the
 * leader's screen asks for. Inert until the server is started — no queries,
 * no events — so a church that never uses band screens pays nothing.
 *
 * A leader command (song, slide, blank) is applied through the same actions
 * the operator's own clicks use, so it counts as a manual move everywhere else (recording, the
 * AI's override handling) and the operator grid follows it like any click.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { parseStoredChart, type ChordChart } from "@/lib/chords/chart";
import { semitonesToKey, transposeKey } from "@/lib/chords/chord";
import { isInTauri } from "@/lib/invoke";
import {
  BAND_CLIENTS_EVENT,
  BAND_COMMAND_EVENT,
  clampTranspose,
  isBandCommand,
  type BandCommand,
  type BandPosition,
  type BandSetlist,
} from "@/lib/band/protocol";
import type { BlankMode, PresentationContext } from "@/lib/presentation/state";
import { onlineRoom } from "@/lib/band/onlineRoom";
import { hostedRoom, type HostedRoomState } from "@/lib/band/hostedRoom";
import { accountStatus, liveOrganization, type Membership } from "@/lib/account";
import { FEATURES } from "@/lib/features";
import { useOnlineRoom, type OnlineRoomApi } from "@/lib/band/useOnlineRoom";
import {
  bandClear,
  bandInfo,
  bandPublish,
  bandStart,
  getSongCharts,
  listLibraries,
  type BandInfo,
} from "@/lib/tauri";

/** Remembered across launches: once a church turns band screens on, they come back on. */
const AUTOSTART_KEY = "band_screens_autostart";

function readAutostart(): boolean {
  try {
    return localStorage.getItem(AUTOSTART_KEY) === "1";
  } catch {
    return false;
  }
}

function writeAutostart(on: boolean) {
  try {
    localStorage.setItem(AUTOSTART_KEY, on ? "1" : "0");
  } catch {
    // private mode — the operator just clicks the button again next time
  }
}

export interface BandHostOptions {
  ctx: PresentationContext;
  itemIdx: number;
  slideIdx: number;
  blankMode: BlankMode;
  jumpTo: (itemIdx: number, slideIdx: number) => void;
  /**
   * Insert a library song right after the current one and persist it,
   * resolving to the new setlist item's id (null if it failed). Absent where
   * the set can't be edited live — the leader's screen then hides the option.
   */
  addSong?: (songId: string) => Promise<string | null>;
  /** The view's blank toggle — the leader's blank/show goes through it. */
  toggleBlank?: (mode: Exclude<BlankMode, "none">) => void;
  /**
   * Save a song's key for this service (null: its own key) — the same change
   * as the key badge in the view. The leader's transpose goes through it, so
   * the key they pick is kept with the setlist and shows on the operator's
   * screen. Without it a transpose lasts only while the service runs.
   */
  setKey?: (itemId: string, key: string | null) => void;
}

export interface BandHostApi {
  /** Null until band screens are switched on. */
  info: BandInfo | null;
  clients: number;
  /** Switch the local band server on; `remember` (default) brings it back next launch. */
  start: (remember?: boolean) => Promise<void>;
  error: string | null;
  /** The online room: for when devices can't reach this computer. App-wide,
   * so one started in the setlist editor carries on into the service. */
  online?: OnlineRoomApi;
  /** The organization's hosted live room, when this computer is signed in to one. */
  hosted?: HostedRoomState;
}

/**
 * Semitones from a chart's own key to the key the service plays it in, the
 * short way round (G → Bb is +3, G → E is -3). Exported for tests.
 */
export function serviceTranspose(chart: ChordChart | null, songKey: string | null | undefined): number {
  const up = semitonesToKey(chart?.key, songKey);
  return up > 6 ? up - 12 : up;
}

/** Build the setlist snapshot. Exported for tests. */
export function buildBandSetlist(
  ctx: PresentationContext,
  charts: Record<string, ChordChart | null>,
  transposes: Record<string, number>,
  canAddSongs = false,
): BandSetlist {
  return {
    setlistId: ctx.setlistId,
    name: ctx.setlistName,
    canAddSongs,
    songs: ctx.items.map((it) => ({
      itemId: it.itemId,
      songId: it.song.id,
      title: it.song.title,
      author: it.song.author,
      slides: it.slides.map((s) => ({ section: s.section, lyrics: s.lyrics })),
      chart: charts[it.song.id] ?? null,
      transpose: transposes[it.itemId] ?? serviceTranspose(charts[it.song.id] ?? null, it.songKey),
    })),
  };
}

/** What a leader command asks the operator to do, or null if it's stale. Exported for tests. */
export function resolveBandCommand(
  command: BandCommand,
  ctx: PresentationContext,
):
  | { type: "jump"; itemIdx: number; slideIdx: number }
  | { type: "transpose"; itemId: string; semitones: number }
  | { type: "add"; songId: string; goNow: boolean; key: string | null }
  | { type: "blank"; on: boolean }
  | null {
  if (command.kind === "add_song") {
    return { type: "add", songId: command.songId, goNow: command.when === "now", key: command.key ?? null };
  }
  if (command.kind === "set_blank") return { type: "blank", on: command.on };
  const idx = ctx.items.findIndex((it) => it.itemId === command.itemId);
  if (idx < 0) return null; // the song was removed after the leader's screen drew it
  // Slide 0 is the blank that opens every song.
  if (command.kind === "go_to_song") return { type: "jump", itemIdx: idx, slideIdx: 0 };
  if (command.kind === "go_to_slide") {
    // The song was edited after the leader's screen drew it.
    if (command.slideIdx >= ctx.items[idx]!.slides.length) return null;
    return { type: "jump", itemIdx: idx, slideIdx: command.slideIdx };
  }
  return { type: "transpose", itemId: command.itemId, semitones: clampTranspose(command.semitones) };
}

/**
 * Just the band server: switch it on, and what devices need to join (address,
 * PIN, how many are connected). Publishes nothing, so it's safe outside a
 * service — the set editor uses it so the band and leader can connect their
 * devices before the service starts (they wait on "Waiting for the service
 * to start" until it does). `useBandHost` builds on it.
 */
export function useBandServer(): BandHostApi {
  const [info, setInfo] = useState<BandInfo | null>(null);
  const [clients, setClients] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const running = info !== null;

  const start = useCallback(async (remember = true) => {
    try {
      const i = await bandStart();
      setInfo(i);
      setClients(i.clients);
      setError(null);
      if (remember) writeAutostart(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Pick up a server already running (started from another view), or bring
  // it back if the church had it on last time.
  useEffect(() => {
    if (!isInTauri()) return;
    let cancelled = false;
    void bandInfo()
      .then((i) => {
        if (cancelled) return;
        if (i) {
          setInfo(i);
          setClients(i.clients);
        } else if (readAutostart()) {
          void start();
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [start]);

  // Device count, straight from the server.
  useEffect(() => {
    if (!running || !isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const un = await listen<number>(BAND_CLIENTS_EVENT, (e) => setClients(e.payload));
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [running]);

  const online = useOnlineRoom({
    // Same PIN as the local server, so a leader's saved PIN works either way.
    getPin: async () => (await bandInfo())?.pin ?? (await bandStart()).pin,
  });

  return useMemo(() => ({ info, clients, start, error, online }), [info, clients, start, error, online]);
}

export function useBandHost(opts: BandHostOptions): BandHostApi {
  const { ctx, itemIdx, slideIdx, blankMode, jumpTo, addSong, toggleBlank, setKey } = opts;
  const server = useBandServer();
  const { info } = server;
  const [charts, setCharts] = useState<Record<string, ChordChart | null>>({});
  const [transposes, setTransposes] = useState<Record<string, number>>({});

  // Leader commands come from the local server (a Tauri event) and from the
  // online room; both land here. Set below, once everything it needs exists.
  const applyCommandRef = useRef<(command: unknown) => void>(() => undefined);
  const online = server.online!;
  useEffect(() => {
    onlineRoom.setCommandHandler((c) => applyCommandRef.current(c));
    return () => onlineRoom.setCommandHandler(null);
  }, []);
  // Signed in to an organization that runs the live set: its hosted room
  // opens on its own, with the local band server alongside as the fallback.
  const [liveOrg, setLiveOrg] = useState<Membership | null>(null);
  useEffect(() => {
    // Outside the app (browser preview) this is the mock account.
    if (!FEATURES.account) return;
    let live = true;
    // The open library decides: a service goes live where its songs are.
    Promise.all([accountStatus(), listLibraries().catch(() => [])]).then(
      ([s, libraries]) => live && setLiveOrg(liveOrganization(s, libraries.find((l) => l.open)?.id)),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  const hosted = useSyncExternalStore(hostedRoom.subscribe, hostedRoom.getState);
  const startServer = server.start;
  useEffect(() => {
    if (!liveOrg) return;
    hostedRoom.start({ orgId: liveOrg.orgId, orgName: liveOrg.orgName });
    if (!info) void startServer(false);
    // `info` is checked only when the organization is picked: the server
    // stays on once started.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveOrg, startServer]);
  useEffect(() => {
    hostedRoom.setCommandHandler((c) => applyCommandRef.current(c));
    return () => hostedRoom.setCommandHandler(null);
  }, []);

  const localRunning = info !== null;
  const onlineActive = online.room !== null;
  const hostedActive = hosted.org !== null;
  // Publishing (and fetching charts) while any path is on.
  const running = localRunning || onlineActive || hostedActive;

  // Charts for the songs in the service.
  const songIdsKey = useMemo(() => [...new Set(ctx.items.map((it) => it.song.id))].sort().join(","), [ctx.items]);
  useEffect(() => {
    if (!running || !songIdsKey) return;
    let cancelled = false;
    void getSongCharts(songIdsKey.split(","))
      .then((rows) => {
        if (cancelled) return;
        const out: Record<string, ChordChart | null> = {};
        for (const [id, row] of Object.entries(rows)) out[id] = parseStoredChart(row.chart_json);
        setCharts(out);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [running, songIdsKey]);

  const canAddSongs = addSong !== undefined;
  const setlist = useMemo(
    () => buildBandSetlist(ctx, charts, transposes, canAddSongs),
    [ctx, charts, transposes, canAddSongs],
  );
  useEffect(() => {
    if (localRunning) void bandPublish("setlist", setlist).catch(() => undefined);
    if (onlineActive) onlineRoom.publish("setlist", setlist);
    if (hostedActive) hostedRoom.publish("setlist", setlist);
  }, [localRunning, onlineActive, hostedActive, setlist]);

  const itemId = ctx.items[itemIdx]?.itemId ?? null;
  useEffect(() => {
    const position: BandPosition = { itemIdx, slideIdx, itemId, blankMode };
    if (localRunning) void bandPublish("position", position).catch(() => undefined);
    if (onlineActive) onlineRoom.publish("position", position);
    if (hostedActive) hostedRoom.publish("position", position);
  }, [localRunning, onlineActive, hostedActive, itemIdx, slideIdx, itemId, blankMode]);

  // Where devices in the hosted room can reach this computer if the room
  // can't be reached; the leader's address carries the local PIN.
  const lanBase = info?.urls[0] ?? null;
  const lanPin = info?.pin ?? null;
  useEffect(() => {
    if (hostedActive && lanBase && lanPin) {
      hostedRoom.publish("lan", { band: `${lanBase}/band`, leader: `${lanBase}/leader?pin=${lanPin}` });
    }
  }, [hostedActive, lanBase, lanPin]);

  // Devices that fell back to this computer go back to the hosted room as
  // soon as it's reachable again.
  const hostedLive = hosted.connection === "live";
  useEffect(() => {
    if (localRunning && hostedActive) void bandPublish("hosted", { live: hostedLive }).catch(() => undefined);
  }, [localRunning, hostedActive, hostedLive]);

  // Leader commands. Refs so the listener is registered once, not per render.
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const jumpToRef = useRef(jumpTo);
  jumpToRef.current = jumpTo;
  const addSongRef = useRef(addSong);
  addSongRef.current = addSong;
  const toggleBlankRef = useRef(toggleBlank);
  toggleBlankRef.current = toggleBlank;
  const blankModeRef = useRef(blankMode);
  blankModeRef.current = blankMode;
  const setKeyRef = useRef(setKey);
  setKeyRef.current = setKey;
  const chartsRef = useRef(charts);
  chartsRef.current = charts;

  // "Go now" on a song the leader just added: the set has to be saved and
  // rebuilt before there's a slide to jump to (`jumpTo` clamps to the
  // current context), so wait for the new item to show up.
  const [pendingJumpItemId, setPendingJumpItemId] = useState<string | null>(null);
  useEffect(() => {
    if (!pendingJumpItemId) return;
    const idx = ctx.items.findIndex((it) => it.itemId === pendingJumpItemId);
    if (idx < 0) return;
    setPendingJumpItemId(null);
    jumpToRef.current(idx, 0);
  }, [ctx, pendingJumpItemId]);
  applyCommandRef.current = (payload: unknown) => {
    if (!isBandCommand(payload)) return;
    const action = resolveBandCommand(payload, ctxRef.current);
    if (action?.type === "jump") {
      jumpToRef.current(action.itemIdx, action.slideIdx);
    } else if (action?.type === "blank") {
      // "Blank" means black; "show" clears whichever blank is up (the
      // operator may have used white).
      const current = blankModeRef.current;
      if (action.on && current !== "black") toggleBlankRef.current?.("black");
      else if (!action.on && current !== "none") toggleBlankRef.current?.(current);
    } else if (action?.type === "transpose") {
      // Kept as the song's key for this service when the chart says what key
      // it's in; otherwise only for as long as the service runs.
      const item = ctxRef.current.items.find((it) => it.itemId === action.itemId);
      const chartKey = item ? chartsRef.current[item.song.id]?.key : null;
      const save = setKeyRef.current;
      if (save && chartKey) {
        save(action.itemId, action.semitones % 12 === 0 ? null : transposeKey(chartKey, action.semitones));
        setTransposes(({ [action.itemId]: _dropped, ...rest }) => rest);
      } else {
        setTransposes((prev) => ({ ...prev, [action.itemId]: action.semitones }));
      }
    } else if (action?.type === "add") {
      const add = addSongRef.current;
      if (!add) return;
      void add(action.songId).then((newItemId) => {
        // "Fits next" moved it: keep that key with the setlist, like a badge change.
        if (newItemId && action.key) setKeyRef.current?.(newItemId, action.key);
        if (newItemId && action.goNow) setPendingJumpItemId(newItemId);
      });
    }
  };
  useEffect(() => {
    if (!localRunning || !isInTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const un = await listen<unknown>(BAND_COMMAND_EVENT, (e) => applyCommandRef.current(e.payload));
      if (cancelled) un();
      else unlisten = un;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [localRunning]);

  // The service ended: tell the band's screens there's nothing to follow —
  // on the local server and in the online room, which both stay open.
  useEffect(() => {
    return () => {
      if (isInTauri()) void bandClear().catch(() => undefined);
      onlineRoom.idle();
      hostedRoom.idle();
    };
  }, []);

  return useMemo(() => ({ ...server, hosted: hostedActive ? hosted : undefined }), [server, hosted, hostedActive]);
}
