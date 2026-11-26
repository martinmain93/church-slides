/**
 * The band screen: what a musician sees on their phone or tablet.
 *
 * Served by the desktop app's band server to any browser on the church
 * network (`/band`), and with leader controls at `/leader`. It follows the
 * operator: the current song's arrangement, chords over lyrics, the slide on
 * screen highlighted and kept in view. The leader can also tap a slide to put
 * it on the projector, and blank the projector — enough to run a service with
 * nobody at the laptop.
 *
 * Everything a musician changes here (text size, chords on/off, capo) is
 * theirs alone and stays on the device. The song, position and key are shared
 * and come from the operator.
 */

import { JumpHoldBar } from "@/components/band/JumpHoldBar";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Monitor, MonitorOff } from "lucide-react";

import {
  bandInviteUrl,
  hostedLink,
  safeLanUrl,
  type MemberSource,
  useBandConnection,
  type BandConnection,
  type ConnectionStatus,
} from "@/lib/band/useBandConnection";
import { clampTranspose, type BandSong } from "@/lib/band/protocol";
import {
  alignSlides,
  displayKey,
  transposeLines,
  type AlignedSlide,
  type ChartLine,
} from "@/lib/chords/chart";
import { parseChord, transposeKey } from "@/lib/chords/chord";
import { isBlankSlide } from "@/lib/slideSequence";
import type { ParsedSection } from "@/lib/parseSong";
import { LibrarySheet, openAndFocus, SetSheet, useConfirmTap } from "@/components/band/LeaderSheets";
import { ChordLyrics } from "@/components/band/ChordLyrics";
import { QrCode } from "@/components/band/QrCode";
import { localBandWindowLabel, toggleOwnFullscreen } from "@/lib/band/localWindow";
import { glideScrollLeft } from "@/lib/band/glide";

function sectionLabel(s: ParsedSection): string {
  const base = s.type.replace("-", " ");
  if (s.type === "verse" || s.number > 1) return `${base} ${s.number}`;
  return base;
}

// --- per-device preferences ---------------------------------------------------

interface DevicePrefs {
  /** Text scale, 0.7–2. */
  scale: number;
  showChords: boolean;
  /** Guitarist's capo: chords are shown as the shapes to play. */
  capo: number;
}

// Windows on the operator's computer share one browser storage, so each keeps
// its own prefs under its label — two team members on two monitors can have
// different capos.
const PREFS_KEY = localBandWindowLabel() ? `band_screen_prefs:${localBandWindowLabel()}` : "band_screen_prefs";
const DEFAULT_PREFS: DevicePrefs = { scale: 1, showChords: true, capo: 0 };

function loadPrefs(): DevicePrefs {
  try {
    return { ...DEFAULT_PREFS, ...(JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") as Partial<DevicePrefs>) };
  } catch {
    return DEFAULT_PREFS;
  }
}

function savePrefs(p: DevicePrefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    // private browsing — prefs just won't survive a reload
  }
}

function usePrefs(): [DevicePrefs, (patch: Partial<DevicePrefs>) => void] {
  const [prefs, setPrefs] = useState(loadPrefs);
  const update = (patch: Partial<DevicePrefs>) =>
    setPrefs((p) => {
      const next = { ...p, ...patch };
      savePrefs(next);
      return next;
    });
  return [prefs, update];
}

// --- screen wake lock ---------------------------------------------------------

/**
 * Ask the browser to keep the screen on. Only works over HTTPS or localhost,
 * which a LAN address isn't — so on most devices this quietly fails and the
 * page shows a hint instead. Re-requested whenever the page comes back.
 */
function useWakeLock(): boolean {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    const nav = navigator as Navigator & {
      wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void>; addEventListener: (e: string, f: () => void) => void }> };
    };
    if (!nav.wakeLock) return;
    let lock: { release: () => Promise<void> } | null = null;
    let cancelled = false;
    const request = async () => {
      try {
        const l = await nav.wakeLock!.request("screen");
        if (cancelled) return void l.release();
        lock = l;
        setHeld(true);
        l.addEventListener("release", () => setHeld(false));
      } catch {
        setHeld(false);
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void request();
    };
    void request();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      void lock?.release().catch(() => undefined);
    };
  }, []);
  return held;
}

// --- role & PIN -----------------------------------------------------------------

const PIN_KEY = "band_leader_pin";

function initialRole(): "band" | "leader" {
  const params = new URLSearchParams(window.location.search);
  // "/leader" on the slides computer's server, "/r/<room>/leader" on the online relay.
  return /(^|\/)leader$/.test(window.location.pathname) || params.get("window") === "leader" ? "leader" : "band";
}

function initialPin(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("pin");
  if (fromUrl) {
    try {
      localStorage.setItem(PIN_KEY, fromUrl);
    } catch {
      /* ignore */
    }
    return fromUrl;
  }
  try {
    return localStorage.getItem(PIN_KEY);
  } catch {
    return null;
  }
}

// --- view -------------------------------------------------------------------------

/** How long the hosted room can be out of reach before offering the church's network. */
const FALLBACK_AFTER_MS = 10_000;

/**
 * Between the hosted room and the slides computer's own band server.
 *
 * A page from the website (https) can't open a socket to the slides
 * computer on the church's network (http): browsers block that. So when the
 * hosted room is out of reach, offer to *go* to the slides computer's band
 * page, remembering the way back; that page comes straight back here as soon
 * as the slides computer says the hosted room is reachable again.
 */
function NetworkFallback({ conn, hosted }: { conn: BandConnection; hosted: boolean }) {
  const [offer, setOffer] = useState(false);
  const { status, lanUrl, hostedLive } = conn;

  useEffect(() => {
    if (!hosted || status === "live" || !lanUrl) {
      setOffer(false);
      return;
    }
    const t = setTimeout(() => setOffer(true), FALLBACK_AFTER_MS);
    return () => clearTimeout(t);
  }, [hosted, status, lanUrl]);

  const back = useMemo(() => (hosted ? null : new URLSearchParams(window.location.hash.slice(1)).get("back")), [hosted]);
  useEffect(() => {
    if (back && hostedLive && /^https:\/\//.test(back)) window.location.replace(back);
  }, [back, hostedLive]);

  if (!offer || !lanUrl) return null;
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2 text-sm">
      <span>Can&apos;t reach the internet. On the church&apos;s Wi-Fi?</span>
      <button
        type="button"
        className="shrink-0 rounded-md border border-border px-3 py-1"
        onClick={() => {
          const safe = safeLanUrl(lanUrl);
          if (safe) window.location.href = `${safe.split("#")[0]}#back=${encodeURIComponent(window.location.href)}`;
        }}
      >
        Connect directly
      </button>
    </div>
  );
}

/** Signed in on the website's band page instead of opening a band link. */
export interface MemberView {
  source: MemberSource;
  orgName: string;
  onSignOut: () => void;
}

export function BandView({ member }: { member?: MemberView } = {}) {
  // A hosted band link, or a signed-in member: the room says whether this
  // device gets leader controls.
  const hosted = useMemo(() => member !== undefined || hostedLink() !== null, [member]);
  const requestedRole = useMemo(initialRole, []);
  const [pin, setPin] = useState<string | null>(() => (!hosted && requestedRole === "leader" ? initialPin() : null));
  const conn = useBandConnection(hosted ? "leader" : requestedRole, pin, member?.source);
  const role: "band" | "leader" = hosted ? (conn.isLeader ? "leader" : "band") : requestedRole;
  const [prefs, setPrefs] = usePrefs();
  const [showSettings, setShowSettings] = useState(false);
  const [sheet, setSheet] = useState<"set" | "library" | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const wakeLock = useWakeLock();

  // A band screen window on the operator's computer is a display nobody sits
  // at: F toggles fullscreen, and the pointer hides itself when it stops on
  // the screen after the operator drags or clicks something there.
  useEffect(() => {
    if (!localBandWindowLabel()) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "f" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      void toggleOwnFullscreen();
    };
    let idle: ReturnType<typeof setTimeout> | undefined;
    const onMove = () => {
      document.documentElement.classList.remove("cursor-hidden");
      clearTimeout(idle);
      idle = setTimeout(() => document.documentElement.classList.add("cursor-hidden"), 2500);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousemove", onMove);
    onMove();
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousemove", onMove);
      clearTimeout(idle);
    };
  }, []);

  // Fetch the library as soon as the leader is in, so search is instant when
  // they need it mid-service.
  const { isLeader, library, requestLibrary } = conn;
  useEffect(() => {
    if (isLeader && !library) requestLibrary();
  }, [isLeader, library, requestLibrary]);

  const { setlist, position } = conn;
  const song: BandSong | undefined =
    setlist && position
      ? setlist.songs.find((s) => s.itemId === position.itemId) ?? setlist.songs[position.itemIdx]
      : undefined;
  const songIdx = song && setlist ? setlist.songs.indexOf(song) : -1;
  const nextSong = setlist && songIdx >= 0 ? setlist.songs[songIdx + 1] : undefined;
  const midSong = Boolean(song && position && position.slideIdx > 0 && position.slideIdx < song.slides.length - 1);
  const inSet = useMemo(() => new Map((setlist?.songs ?? []).map((s) => [s.songId, s.itemId])), [setlist]);
  const leaderReady = role === "leader" && conn.isLeader && setlist !== null;

  useEffect(() => {
    document.title = song ? `${song.title} — Band` : "Band screen";
  }, [song]);

  if (conn.linkRemoved) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-2 bg-background p-6 text-center text-foreground supports-[height:100dvh]:h-[100dvh]">
        {member ? (
          <>
            <p className="text-lg font-medium">You can&apos;t follow {member.orgName} any more</p>
            <p className="text-sm text-muted-foreground">You were signed out, or you&apos;re no longer in this organization.</p>
            <button type="button" className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm" onClick={member.onSignOut}>
              Sign in again
            </button>
          </>
        ) : (
          <>
            <p className="text-lg font-medium">This band link no longer works</p>
            <p className="text-sm text-muted-foreground">Ask whoever runs the slides for a new one.</p>
          </>
        )}
      </div>
    );
  }

  return (
    <div
      className="flex h-screen flex-col bg-background text-foreground supports-[height:100dvh]:h-[100dvh]"
      style={{ fontSize: `${16 * prefs.scale}px` }}
    >
      <NetworkFallback conn={conn} hosted={hosted} />
      <Header
        song={song}
        nextSong={nextSong}
        status={conn.status}
        capo={prefs.capo}
        onCapo={(capo) => setPrefs({ capo })}
        onTranspose={
          leaderReady && song ? (semitones) => conn.send({ kind: "set_transpose", itemId: song.itemId, semitones }) : undefined
        }
        blanked={position?.blankMode !== undefined && position.blankMode !== "none"}
        onSettings={() => setShowSettings((v) => !v)}
        onBlank={leaderReady ? (on) => conn.send({ kind: "set_blank", on }) : undefined}
      />
      {leaderReady && sheet === "set" && (
        <SetSheet
          songs={setlist.songs}
          setName={setlist.name}
          currentIdx={songIdx}
          midSong={midSong}
          canAddSongs={setlist.canAddSongs}
          onGo={(itemId) => conn.send({ kind: "go_to_song", itemId })}
          onOpenLibrary={() => {
            requestLibrary(); // refresh in the background; the cached copy shows now
            openAndFocus(() => setSheet("library"), searchRef);
          }}
          onClose={() => setSheet(null)}
        />
      )}
      {leaderReady && sheet === "library" && (
        <LibrarySheet
          library={library}
          current={song}
          inSet={inSet}
          midSong={midSong}
          inputRef={searchRef}
          onAdd={(songId, when, key) => {
            conn.send(key ? { kind: "add_song", songId, when, key } : { kind: "add_song", songId, when });
            setSheet(null);
          }}
          onGoExisting={(itemId) => {
            conn.send({ kind: "go_to_song", itemId });
            setSheet(null);
          }}
          onBack={() => setSheet("set")}
          onClose={() => setSheet(null)}
        />
      )}
      {showSettings && (
        <Settings
          prefs={prefs}
          onChange={setPrefs}
          wakeLock={wakeLock}
          role={role}
          isLeader={conn.isLeader}
          hosted={hosted}
          member={member}
        />
      )}
      {role === "leader" && !conn.isLeader && (
        <PinPrompt
          rejected={conn.pinRejected}
          tooManyTries={conn.tooManyTries}
          onSubmit={(p) => {
            try {
              localStorage.setItem(PIN_KEY, p);
            } catch {
              /* ignore */
            }
            setPin(p);
          }}
        />
      )}
      {role === "leader" && conn.isLeader && setlist && (
        <LeaderPalette
          songs={setlist.songs}
          currentIdx={songIdx}
          midSong={midSong}
          onGo={(itemId) => conn.send({ kind: "go_to_song", itemId })}
          onAllSongs={() => setSheet("set")}
        />
      )}
      <main className="min-h-0 flex-1 overflow-y-auto px-4 pb-24 pt-3">
        {conn.idle || !setlist ? (
          <Waiting status={conn.status} idle={conn.idle} hosted={hosted} />
        ) : song && position ? (
          <SongChart
            key={song.itemId}
            song={song}
            currentSlide={position.slideIdx}
            prefs={prefs}
            onPick={
              leaderReady
                ? (slideIdx) => conn.send({ kind: "go_to_slide", itemId: song.itemId, slideIdx })
                : undefined
            }
          />
        ) : (
          <Waiting status={conn.status} idle={false} hosted={hosted} />
        )}
      </main>
      {leaderReady && conn.jumpHold && (
        <JumpHoldBar
          hold={conn.jumpHold}
          onAnswer={(go) => conn.send({ kind: "jump_answer", id: conn.jumpHold!.id, go })}
        />
      )}
      {conn.lastError && (
        <div className="border-t border-destructive bg-destructive/20 px-4 py-2 text-sm">{conn.lastError}</div>
      )}
    </div>
  );
}

function statusDot(status: ConnectionStatus) {
  const cls =
    status === "live" ? "bg-emerald-500" : status === "connecting" ? "bg-amber-400" : "bg-red-500 animate-pulse";
  const label = status === "live" ? "Connected" : status === "connecting" ? "Connecting" : "Reconnecting…";
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground" title={label}>
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} />
      {status !== "live" && label}
    </span>
  );
}

function Header({
  song,
  nextSong,
  status,
  capo,
  onCapo,
  onTranspose,
  blanked,
  onSettings,
  onBlank,
}: {
  song: BandSong | undefined;
  nextSong: BandSong | undefined;
  status: ConnectionStatus;
  capo: number;
  /** This device's capo (a guitarist's own; nobody else sees it). */
  onCapo: (capo: number) => void;
  /** Leader only: move the band to another key. */
  onTranspose?: (semitones: number) => void;
  blanked: boolean;
  onSettings: () => void;
  /** Leader only: blank the projector, or bring the lyrics back. */
  onBlank?: (on: boolean) => void;
}) {
  const key = song ? displayKey(song.chart, song.transpose) : null;
  const shapes = key && capo ? transposeKey(key, -capo) : null;
  return (
    // `relative`: the key menu hangs from the header's right edge, not the
    // key's — on a phone the key sits mid-header and a menu aligned to it
    // ran off the left of the screen.
    <header className="relative flex items-center gap-3 border-b border-border px-4 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[1.25em] font-semibold leading-tight">{song?.title ?? "—"}</div>
        <div className="truncate text-[0.8em] text-muted-foreground">
          {nextSong ? `Next: ${nextSong.title}` : song ? "Last song" : ""}
          {blanked && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-foreground">Screen blanked</span>}
        </div>
      </div>
      {song?.chart && (
        <KeyMenu song={song} capo={capo} shapes={shapes} onCapo={onCapo} onTranspose={onTranspose} />
      )}
      {statusDot(status)}
      {onBlank && (
        <button
          type="button"
          onClick={() => onBlank(!blanked)}
          className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-sm ${
            blanked ? "border-amber-400 bg-amber-400/20" : "border-border"
          }`}
          aria-pressed={blanked}
          title={blanked ? "Put the lyrics back on the screen" : "Blank the screen"}
          data-testid="leader-blank"
        >
          {blanked ? <Monitor className="size-5" /> : <MonitorOff className="size-5" />}
          {blanked ? "Show" : "Blank"}
        </button>
      )}
      <button
        type="button"
        onClick={onSettings}
        className="rounded-md border border-border px-2.5 py-1.5 text-sm"
        aria-label="Display settings"
      >
        Aa
      </button>
    </header>
  );
}

/** The 12 keys a song can move to, each spelled as a band would write it, with the transpose to get there. Exported for tests. */
export function keyChoices(writtenKey: string): { key: string; semitones: number }[] {
  const out: { key: string; semitones: number; pc: number }[] = [];
  for (let t = -5; t <= 6; t++) {
    const k = transposeKey(writtenKey, t);
    out.push({ key: k, semitones: t, pc: parseChord(k)?.root ?? 0 });
  }
  return out.sort((a, b) => a.pc - b.pc).map(({ key, semitones }) => ({ key, semitones }));
}

/**
 * The key in the header, as a button: drops down the band's key (the leader
 * changes it for everyone) and this device's capo.
 */
function KeyMenu({
  song,
  capo,
  shapes,
  onCapo,
  onTranspose,
}: {
  song: BandSong;
  capo: number;
  shapes: string | null;
  onCapo: (capo: number) => void;
  onTranspose?: (semitones: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const written = song.chart?.key ?? null;
  const key = displayKey(song.chart, song.transpose);
  const pill = "rounded-md border px-2 py-1.5 text-center text-sm";
  return (
    <div ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-md px-1.5 py-1 text-right leading-tight hover:bg-muted"
        aria-expanded={open}
        aria-label={key ? `Key ${key} — change key or capo` : "Capo"}
        data-testid="key-button"
      >
        <span>
          <span className="block text-[1.25em] font-bold" data-testid="band-key">
            {key ?? "Key"}
          </span>
          {shapes && (
            <span className="block text-[0.7em] text-muted-foreground">
              capo {capo}: {shapes} shapes
            </span>
          )}
        </span>
        <ChevronDown className="size-4 text-muted-foreground" />
      </button>
      {open && (
        <div
          className="absolute right-2 top-full z-30 mt-1 w-72 max-w-[calc(100%-1rem)] rounded-lg border border-border bg-background p-3 text-sm shadow-xl"
          data-testid="key-menu"
        >
          {written && (
            <>
              <div className="mb-1.5 flex items-baseline justify-between text-xs text-muted-foreground">
                <span>Key for {song.title}</span>
                {!onTranspose && <span>set by the worship leader</span>}
              </div>
              <div className="grid grid-cols-4 gap-1.5">
                {keyChoices(written).map((c) => {
                  const current = c.key === key;
                  return (
                    <button
                      key={c.key}
                      type="button"
                      disabled={!onTranspose}
                      onClick={() => {
                        onTranspose?.(clampTranspose(c.semitones));
                        setOpen(false);
                      }}
                      className={`${pill} flex h-11 flex-col items-center justify-center leading-none ${
                        current ? "border-emerald-500 bg-emerald-500/15 font-semibold" : "border-border"
                      } ${onTranspose ? "" : "opacity-60"}`}
                      aria-pressed={current}
                      data-testid="key-choice"
                    >
                      {c.key}
                      {c.semitones === 0 && <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">written</span>}
                    </button>
                  );
                })}
              </div>
            </>
          )}
          <div className="mb-1.5 mt-3 flex items-baseline justify-between text-xs text-muted-foreground">
            <span>Capo (this device only)</span>
            {shapes && <span>play {shapes} shapes</span>}
          </div>
          <div className="grid grid-cols-8 gap-1">
            {Array.from({ length: 8 }, (_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onCapo(i)}
                className={`${pill} px-0 ${i === capo ? "border-emerald-500 bg-emerald-500/15 font-semibold" : "border-border"}`}
                aria-pressed={i === capo}
                aria-label={i === 0 ? "No capo" : `Capo ${i}`}
                data-testid="capo-choice"
              >
                {i === 0 ? "–" : i}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Settings({
  prefs,
  onChange,
  wakeLock,
  role,
  isLeader,
  hosted,
  member,
}: {
  prefs: DevicePrefs;
  onChange: (p: Partial<DevicePrefs>) => void;
  wakeLock: boolean;
  role: "band" | "leader";
  isLeader: boolean;
  hosted: boolean;
  member?: MemberView;
}) {
  const btn = "rounded-md border border-border px-3 py-1.5 text-sm";
  const local = localBandWindowLabel() !== null;
  const [inviting, setInviting] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/40 px-4 py-3 text-sm">
      <span className="flex items-center gap-1.5">
        Text
        <button type="button" className={btn} onClick={() => onChange({ scale: Math.max(0.7, +(prefs.scale - 0.1).toFixed(1)) })}>
          A−
        </button>
        <button type="button" className={btn} onClick={() => onChange({ scale: Math.min(2, +(prefs.scale + 0.1).toFixed(1)) })}>
          A+
        </button>
      </span>
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={prefs.showChords} onChange={(e) => onChange({ showChords: e.target.checked })} />
        Chords
      </label>
      {local && (
        <button type="button" className={btn} onClick={() => void toggleOwnFullscreen()} data-testid="band-fullscreen">
          Full screen
        </button>
      )}
      {/* The operator's own windows share the QR codes on its Band screens panel. */}
      {member && (
        <button type="button" className={btn} onClick={member.onSignOut} data-testid="band-sign-out">
          Sign out
        </button>
      )}
      {!local && !member && (
        <button type="button" className={btn} onClick={() => setInviting((v) => !v)} aria-pressed={inviting} data-testid="band-invite">
          Invite
        </button>
      )}
      {inviting && <Invite url={bandInviteUrl()} />}
      {/* A window on the operator's computer stays awake with it. */}
      {!wakeLock && !local && (
        <span className="basis-full text-xs text-muted-foreground">
          This browser may let the screen sleep. Set auto-lock to “Never” while you play.
        </span>
      )}
      {local && (
        <span className="basis-full text-xs text-muted-foreground">
          A band screen on this computer. Full screen (or F) on its monitor; each one keeps its own text size and capo.
        </span>
      )}
      {role === "band" && !local && !hosted && (
        <span className="basis-full text-xs text-muted-foreground">
          Leading worship? Open <code>/leader</code> on this address for the song palette.
        </span>
      )}
      {role === "leader" && isLeader && (
        <span className="basis-full text-xs text-muted-foreground">
          Leader controls unlocked. Tap a slide to put it on the screen.
        </span>
      )}
    </div>
  );
}

/** The band link as a QR big enough to scan across a stage, so the next musician joins from here. */
function Invite({ url }: { url: string }) {
  const canShare = typeof navigator.share === "function";
  return (
    <div className="flex basis-full flex-col items-center gap-2 py-2" data-testid="band-invite-panel">
      <QrCode text={url} label="Scan to open the band screen" large />
      <span className="break-all text-center font-mono text-xs text-muted-foreground">{url}</span>
      {canShare && (
        <button
          type="button"
          className="rounded-md border border-border px-3 py-1.5 text-sm"
          onClick={() => void navigator.share({ title: "Band screen", url }).catch(() => {})}
        >
          Share link
        </button>
      )}
    </div>
  );
}

function PinPrompt({
  rejected,
  tooManyTries,
  onSubmit,
}: {
  rejected: boolean;
  tooManyTries?: boolean;
  onSubmit: (pin: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-4 py-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <span>
        {tooManyTries
          ? "Too many wrong PINs. Wait 5 minutes, then try again."
          : rejected
            ? "That PIN didn’t match — check the operator screen."
            : "Enter the leader PIN shown on the operator screen:"}
      </span>
      <input
        inputMode="numeric"
        autoComplete="off"
        className="w-24 rounded-md border border-border bg-background px-2 py-1 text-center tracking-widest"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        aria-label="Leader PIN"
      />
      <button type="submit" className="rounded-md bg-primary px-3 py-1 text-primary-foreground">
        Unlock
      </button>
    </form>
  );
}

function LeaderPalette({
  songs,
  currentIdx,
  midSong,
  onGo,
  onAllSongs,
}: {
  songs: BandSong[];
  currentIdx: number;
  midSong: boolean;
  onGo: (itemId: string) => void;
  /** Open the whole set (and from there, the library). */
  onAllSongs: () => void;
}) {
  const confirm = useConfirmTap(midSong);

  // Each song change brings the current song to the middle of the strip, so
  // it stays in view while the operator moves through the set. Between
  // changes the strip is the leader's to scroll.
  const strip = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = strip.current;
    const btn = el?.querySelector<HTMLElement>("[aria-current]");
    if (!el || !btn) return;
    const max = el.scrollWidth - el.clientWidth;
    const left = btn.offsetLeft - (el.clientWidth - btn.offsetWidth) / 2;
    return glideScrollLeft(el, Math.max(0, Math.min(max, left)));
  }, [currentIdx]);

  return (
    <section className="border-b border-border px-3 pb-1 pt-2" aria-label="Service">
      {/* `relative`: the songs' offsetLeft is measured from the strip. */}
      <div ref={strip} className="relative flex gap-2 overflow-x-auto pb-1" data-testid="palette-strip">
        {songs.map((s, i) => {
          const isCurrent = i === currentIdx;
          const asking = confirm.armed === s.itemId;
          return (
            <button
              key={s.itemId}
              type="button"
              data-testid="palette-song"
              aria-current={isCurrent ? "true" : undefined}
              onClick={() => {
                if (isCurrent && !midSong) return;
                if (confirm.tap(s.itemId)) onGo(s.itemId);
              }}
              className={[
                "min-w-[9em] shrink-0 rounded-lg border px-3 py-2 text-left text-[0.9em] leading-tight",
                isCurrent ? "border-emerald-500 bg-emerald-500/15" : "border-border bg-muted/30",
                asking ? "border-amber-400 bg-amber-400/20" : "",
              ].join(" ")}
            >
              <div className="text-[0.75em] text-muted-foreground">{i + 1}</div>
              <div className="font-medium">{asking ? `Tap again: go to ${s.title}` : s.title}</div>
              {displayKey(s.chart, s.transpose) && (
                <div className="text-[0.75em] text-muted-foreground">{displayKey(s.chart, s.transpose)}</div>
              )}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        onClick={onAllSongs}
        className="mt-1 flex w-full items-center justify-center rounded-md py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label="All songs"
        title="All songs"
        data-testid="all-songs"
      >
        <ChevronDown className="size-5" />
      </button>
    </section>
  );
}

function Waiting({ status, idle, hosted }: { status: ConnectionStatus; idle: boolean; hosted: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted-foreground">
      <div className="text-[1.2em]">
        {status !== "live"
          ? hosted
            ? "Connecting…"
            : "Looking for the slides computer…"
          : idle
            ? "Waiting for the service to start"
            : "Waiting for the first slide"}
      </div>
      {status !== "live" && (
        <div className="max-w-sm text-sm">
          {hosted
            ? "Check this device is online. This page reconnects by itself."
            : "Make sure this device is on the same Wi-Fi as the slides computer. This page reconnects by itself."}
        </div>
      )}
    </div>
  );
}

// --- the chart ------------------------------------------------------------------

function ChordBlock({ lines, show }: { lines: (ChartLine | AlignedSlide["lines"][number])[]; show: boolean }) {
  return (
    <div className="leading-snug">
      {lines.map((line, i) => (
        <ChordLyrics key={i} text={line.text} chords={line.chords} showChords={show} />
      ))}
    </div>
  );
}

function SongChart({
  song,
  currentSlide,
  prefs,
  onPick,
}: {
  song: BandSong;
  currentSlide: number;
  prefs: DevicePrefs;
  /** Leader only: put this slide on the projector. */
  onPick?: (slideIdx: number) => void;
}) {
  const aligned = useMemo<AlignedSlide[]>(
    () =>
      song.chart
        ? alignSlides(song.chart, song.slides)
        : song.slides.map((s) => ({ lines: s.lyrics.split("\n").map((text) => ({ text, chords: [] })), after: [], matched: false })),
    [song],
  );
  // What the player sees: the band's key, then back down by the capo.
  const shift = song.transpose - prefs.capo;
  const shownKey = song.chart?.key ? transposeKey(song.chart.key, shift) : null;

  const refs = useRef<(HTMLElement | null)[]>([]);
  useEffect(() => {
    const el = refs.current[currentSlide];
    // Keep the current slide near the top, with the one before it just
    // visible, so the eye has context and the next lines are on screen.
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [currentSlide, song.itemId]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3">
      {song.slides.map((slide, i) => {
        const a = aligned[i]!;
        const active = i === currentSlide;
        // A plain click, not a pointer-down: a click doesn't fire when the
        // finger was scrolling, so reading down the chart can't move slides.
        const pick =
          onPick && !active
            ? {
                role: "button",
                tabIndex: 0,
                "aria-label": `Show ${isBlankSlide(slide) ? "song start" : sectionLabel(slide.section)}`,
                onClick: () => onPick(i),
                onKeyDown: (e: React.KeyboardEvent) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onPick(i);
                  }
                },
              }
            : {};
        const pickable = onPick && !active ? "cursor-pointer hover:border-border active:bg-muted/50" : "";
        const lines = transposeLines(a.lines, shift, shownKey);
        const blank = isBlankSlide(slide);
        if (blank && a.after.length === 0) {
          return (
            <section
              key={i}
              ref={(el) => { refs.current[i] = el; }}
              data-active={active || undefined}
              {...pick}
              className={`scroll-mt-2 rounded-lg border px-3 py-2 text-sm ${active ? "border-emerald-500 bg-emerald-500/10" : "border-dashed border-border text-muted-foreground"} ${pickable}`}
            >
              Song start
            </section>
          );
        }
        return (
          <section
            key={i}
            ref={(el) => { refs.current[i] = el; }}
            data-active={active || undefined}
            {...pick}
            className={`scroll-mt-2 rounded-lg border px-3 py-2 transition-colors ${
              active ? "border-emerald-500 bg-emerald-500/10" : "border-transparent opacity-80"
            } ${pickable}`}
          >
            {!blank && (
              <div className="mb-1 text-[0.75em] font-semibold uppercase tracking-wide text-muted-foreground">
                {sectionLabel(slide.section)}
              </div>
            )}
            {!blank && <ChordBlock lines={lines} show={prefs.showChords} />}
            {prefs.showChords &&
              a.after.map((run, j) => (
                <div key={j} className="mt-2">
                  <div className="text-[0.75em] font-semibold uppercase tracking-wide text-muted-foreground">{run.label}</div>
                  <ChordBlock lines={transposeLines(run.lines, shift, shownKey)} show />
                </div>
              ))}
          </section>
        );
      })}
    </div>
  );
}
