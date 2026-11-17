/**
 * Operator control for band screens: switch them on, and show the band how
 * to join (a QR code to scan, the address to type, the leader PIN).
 */

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Cloud, MonitorUp, Music } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { BandHostApi } from "@/lib/band/useBandHost";
import { closeBandWindows, countBandWindows, openBandWindow } from "@/lib/tauri";
import { OnlineRoomPanel } from "@/components/band/OnlineRoomPanel";
import type { OnlineRoomApi } from "@/lib/band/useOnlineRoom";
import { QrCode } from "@/components/band/QrCode";
import { cn } from "@/lib/utils";

/** Under the "⋯" menu's "Band screens…" item: how many devices are on band
 * screens once they're on, since there's no top-bar button to say so. */
export function bandScreensMenuHint(band: BandHostApi): string | undefined {
  if (!band.info && !band.online?.room && !band.hosted) return undefined;
  const n = band.clients + (band.online?.clients ?? 0) + (band.hosted?.clients ?? 0);
  const devices = `${n} device${n === 1 ? "" : "s"} connected`;
  if (band.hosted) return `Live at ${band.hosted.org?.orgName ?? "your organization"} · ${devices}`;
  return `On · ${devices}`;
}

/** Signed in to an organization: where the service is going, and where band links come from. */
function HostedRoomNote({ hosted }: { hosted: NonNullable<BandHostApi["hosted"]> }) {
  const live = hosted.connection === "live";
  return (
    <div className="rounded-md border border-border p-3 text-sm" data-testid="hosted-room-note">
      <p>
        {live ? "Live at " : "Connecting to "}
        <span className="font-medium">{hosted.org?.orgName}</span>
        {live && ` · ${hosted.clients} device${hosted.clients === 1 ? "" : "s"}`}
      </p>
      <p className="text-xs text-muted-foreground">
        Band devices with your organization&rsquo;s band link follow the service from anywhere. Make links in Settings →
        Account → Team. Devices on this network can also use the codes below.
      </p>
    </div>
  );
}

export function BandScreensButton({
  band,
  placement = "below",
  align = "left",
  className,
  note,
  openSignal = 0,
  hideButton = false,
}: {
  band: BandHostApi;
  /** Where the panel opens — "above" in the presentation's bottom drawer. */
  placement?: "below" | "above";
  /** Which edge of the button the panel lines up with — "right" near the
   * right edge of the window, so the panel opens leftward. */
  align?: "left" | "right";
  className?: string;
  /** An extra line under the join details — e.g. what devices show before
   * the service starts. */
  note?: string;
  /** Bump to open the panel from elsewhere — a "⋯" menu item. */
  openSignal?: number;
  /** No button at all: the panel opens only from a "⋯" menu item (via
   * `openSignal`), and drops from wherever this sits — so sit it by the menu. */
  hideButton?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // The online room's setup and links take over the whole panel. Once the
  // church has set it up, that's what they came for: open straight onto it.
  const [view, setView] = useState<"main" | "online">("main");
  const onlineReady = Boolean(band.online?.status?.configured || band.online?.room);
  const chose = useRef(false);
  // Closed the online room while the panel's been open: don't reopen it by
  // itself; show Open / Disconnect until the panel is opened again.
  const [closedRoom, setClosedRoom] = useState(false);
  useEffect(() => {
    if (openSignal > 0) setOpen(true);
  }, [openSignal]);
  useEffect(() => {
    if (!open) {
      chose.current = false;
      setClosedRoom(false);
      return;
    }
    // Also when the setup status arrives a moment after opening — unless the
    // operator has already picked a page.
    if (!chose.current) setView(onlineReady ? "online" : "main");
  }, [open, onlineReady]);
  const goTo = (v: "main" | "online") => {
    chose.current = true;
    setView(v);
  };
  const [urlIdx, setUrlIdx] = useState(0);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Close the panel only — the presentation keymap would exit the service.
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const { info, clients, error } = band;
  const [localWindows, setLocalWindows] = useState(0);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    countBandWindows().then(setLocalWindows).catch(() => undefined);
  }, [open, clients]);

  async function openLocal() {
    setLocalError(null);
    try {
      // Through the hook first, so this window knows the server is on.
      await band.start();
      await openBandWindow();
      setLocalWindows(await countBandWindows());
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    }
  }

  const localButton = (
    <div className="flex flex-col gap-1 border-t border-border pt-3">
      <div className="flex gap-2">
        <Button variant="outline" size="sm" className="flex-1 gap-1" onClick={() => void openLocal()} data-testid="band-open-local">
          <MonitorUp className="size-4" />
          Open a band screen on this computer
        </Button>
        {localWindows > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void closeBandWindows().then(() => setLocalWindows(0))}
            data-testid="band-close-local"
            title="Close the band screen windows on this computer's monitors"
          >
            Close {localWindows}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        For a monitor plugged into this computer, for team members without a tablet. It fills a spare monitor by
        itself; with none spare, drag the window there and press F. One per monitor.
      </p>
      {localError && <p className="text-xs text-destructive">{localError}</p>}
    </div>
  );
  const base = info?.urls[Math.min(urlIdx, info.urls.length - 1)] ?? null;

  return (
    <div className="relative" ref={panelRef}>
      {!hideButton && (
        <Button
          variant="outline"
          size="sm"
          className={cn("h-8 gap-1", className)}
          onClick={() => setOpen((v) => !v)}
          title="Lyrics and chords on the band's phones and tablets"
          data-testid="band-screens-button"
        >
          <Music className="size-4" />
          Band screens
          {(info || band.online?.room) && (
            <span className="ml-1 rounded-full bg-emerald-600 px-1.5 text-[10px] font-semibold text-white">
              {clients + (band.online?.clients ?? 0)}
            </span>
          )}
        </Button>
      )}
      {open && (
        <div
          className={cn(
            // Never taller than the window: it scrolls instead of running off
            // the bottom (or top, opening upward) of a small screen.
            "absolute z-50 max-h-[calc(100vh-6rem)] w-[26rem] overflow-y-auto rounded-lg border border-border bg-background p-4 text-foreground shadow-xl",
            placement === "above" ? "bottom-10" : "top-10",
            align === "right" ? "right-0" : "left-0",
          )}
          data-testid="band-panel"
        >
          {view === "online" && band.online ? (
            <div className="flex flex-col gap-3 text-sm">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => goTo("main")}
                  className="flex items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  title="Band screens over this building's Wi-Fi, and on this computer's monitors"
                  data-testid="online-back"
                >
                  <ArrowLeft className="size-4" />
                  Local network
                </button>
                <span className="flex items-center gap-1.5 font-medium">
                  <Cloud className="size-4" />
                  Online room
                </span>
              </div>
              <OnlineRoomPanel online={band.online} autoStart={!closedRoom} onClosed={() => setClosedRoom(true)} />
            </div>
          ) : !info ? (
            <div className="flex flex-col gap-3 text-sm">
              {band.hosted && <HostedRoomNote hosted={band.hosted} />}
              <p>
                Show lyrics and chords on the band&rsquo;s own phones and tablets, following the slides. Everything
                stays on this building&rsquo;s network — no internet needed.
              </p>
              <p className="text-muted-foreground">
                The first time, your computer may ask whether Church Slides can accept incoming network connections —
                allow it.
              </p>
              {error && <p className="text-destructive">{error}</p>}
              <Button size="sm" onClick={() => void band.start()} data-testid="band-start">
                Turn on band screens
              </Button>
              {localButton}
              {band.online && <OnlineRoomEntry online={band.online} onOpen={() => goTo("online")} />}
            </div>
          ) : (
            <div className="flex flex-col gap-3 text-sm">
              {band.hosted && <HostedRoomNote hosted={band.hosted} />}
              <div className="flex justify-around">
                <QrCode text={`${base}/band`} label="Band" />
                <QrCode text={`${base}/leader?pin=${info.pin}`} label="Worship leader" />
              </div>
              <div>
                <div className="text-muted-foreground">Or type this on the device:</div>
                <div className="font-mono text-base" data-testid="band-url">
                  {base}
                </div>
                {info.urls.length > 1 && (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground underline"
                    onClick={() => setUrlIdx((i) => (i + 1) % info.urls.length)}
                    title="This computer is on more than one network"
                  >
                    Not connecting? Try another network address ({urlIdx + 1}/{info.urls.length})
                  </button>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span>
                  Leader PIN: <span className="font-mono text-base font-semibold tracking-widest">{info.pin}</span>
                </span>
                <span className="text-muted-foreground">
                  {clients} device{clients === 1 ? "" : "s"} connected
                </span>
              </div>
              {note && <p className="text-xs">{note}</p>}
              <p className="text-xs text-muted-foreground">
                Devices must be on the same Wi-Fi as this computer. Guest networks often block devices from reaching
                each other — if nothing connects, use the staff network or a small dedicated router for the band.
              </p>
              {localButton}
              {band.online && <OnlineRoomEntry online={band.online} onOpen={() => goTo("online")} />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** The way into the online room from the main panel: a quiet link, or its status once open. */
function OnlineRoomEntry({ online, onOpen }: { online: OnlineRoomApi; onOpen: () => void }) {
  if (!online.status) return null;
  if (online.room) {
    return (
      <button
        type="button"
        onClick={onOpen}
        className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-left text-xs hover:bg-muted"
        data-testid="online-entry"
      >
        <span className="flex items-center gap-1.5">
          <Cloud className="size-3.5" />
          Online room is open — {online.clients} device{online.clients === 1 ? "" : "s"}
        </span>
        <span className="text-muted-foreground">Show codes →</span>
      </button>
    );
  }
  return (
    <button
      type="button"
      className="flex items-center gap-1.5 text-left text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      onClick={onOpen}
      data-testid="online-entry"
    >
      <Cloud className="size-3.5" />
      Devices can&rsquo;t connect? Use an online room instead
    </button>
  );
}
