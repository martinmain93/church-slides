/**
 * Which display each output window is on, and a way to change it.
 *
 * Placement is automatic (see `screens.rs`): this is the correction path for
 * a setup with more displays than the app can guess about — a stream
 * encoder's output, a confidence monitor, a band screen on the projector. It
 * draws the displays as they're arranged, lets each window be sent to
 * another, and marks displays the app should never pick by itself. A move is
 * remembered for next time.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Monitor } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  identifyScreens,
  listScreens,
  moveWindowToScreen,
  setScreenAvoid,
  type ScreenInfo,
} from "@/lib/presentation/projection";
import { cn } from "@/lib/utils";

const POLL_MS = 1500;
const MAP_W = 520;
const MAP_H = 180;

/** "Audience", "Band screen 2". */
export function windowName(label: string): string {
  if (label === "audience") return "Audience";
  const band = /^band-(\d+)$/.exec(label);
  return band ? `Band screen ${band[1]}` : label;
}

/** macOS often reports a display only as "Monitor #41029": number it instead. */
export function displayName(s: ScreenInfo, i: number): string {
  return /^(Monitor #\d+|Display)?$/.test(s.name) ? `Display ${i + 1}` : s.name;
}

function sizeLabel(s: ScreenInfo): string {
  return `${s.pixel_width}×${s.pixel_height}`;
}

export function ScreensDialog({ onClose, onError }: { onClose: () => void; onError: (message: string) => void }) {
  const [screens, setScreens] = useState<ScreenInfo[] | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const doneRef = useRef<HTMLButtonElement | null>(null);
  const say = useRef(onError);
  say.current = onError;

  const refresh = useCallback(() => {
    listScreens()
      .then((s) => setScreens((prev) => (JSON.stringify(prev) === JSON.stringify(s) ? prev : s)))
      .catch((e: unknown) => say.current(e instanceof Error ? e.message : String(e)));
  }, []);

  // Displays come and go, and windows get dragged by hand: keep it current.
  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => doneRef.current?.focus({ preventScroll: true }), []);
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      // Don't let the presentation keymap also see it (it would exit).
      ev.preventDefault();
      ev.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const move = async (label: string, key: string) => {
    setMoving(label);
    try {
      await moveWindowToScreen(label, key);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setMoving(null);
      refresh();
    }
  };

  const avoid = async (key: string, on: boolean) => {
    try {
      await setScreenAvoid(key, on);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  const windows = (screens ?? []).flatMap((s) => s.windows).sort();

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="screens-title"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
      data-testid="screens-dialog"
    >
      <div className="flex max-h-full w-full max-w-xl flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl">
        <div>
          <h2 id="screens-title" className="text-base font-semibold">
            Screens
          </h2>
          <p className="text-xs text-muted-foreground">
            The app picks a display for each screen by itself. Move one here and it goes back there next service.
          </p>
        </div>

        {!screens ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Looking at the displays…
          </div>
        ) : (
          <>
            <ScreenMap screens={screens} />

            <section className="flex flex-col gap-2" aria-label="Windows">
              {windows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No audience or band screen is open.</p>
              ) : (
                windows.map((label) => {
                  const at = screens.find((s) => s.windows.includes(label));
                  return (
                    <label key={label} className="flex items-center gap-3 text-sm">
                      <span className="w-32 shrink-0 font-medium">{windowName(label)}</span>
                      <select
                        className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                        value={at?.key ?? ""}
                        disabled={moving !== null}
                        onChange={(e) => void move(label, e.target.value)}
                        data-testid={`screen-for-${label}`}
                      >
                        {screens.map((s, i) => (
                          <option key={s.key} value={s.key}>
                            {i + 1} · {displayName(s, i)} ({sizeLabel(s)}){s.operator ? " — this screen, in a window" : ""}
                          </option>
                        ))}
                      </select>
                      {moving === label && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
                    </label>
                  );
                })
              )}
            </section>

            <section className="flex flex-col gap-1.5" aria-label="Displays">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Displays</div>
              {screens.map((s, i) => (
                <div key={s.key} className="flex items-center gap-3 text-sm">
                  <span className="w-5 shrink-0 text-right text-muted-foreground">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {displayName(s, i)} <span className="text-muted-foreground">({sizeLabel(s)})</span>
                  </span>
                  {s.operator ? (
                    <span className="text-xs text-muted-foreground">This screen</span>
                  ) : (
                    <label
                      className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      title="Never put a screen here by itself — for a display another program uses, like a stream or a confidence monitor"
                    >
                      <input
                        type="checkbox"
                        checked={s.avoid}
                        onChange={(e) => void avoid(s.key, e.target.checked)}
                        data-testid={`avoid-${i + 1}`}
                      />
                      Don&rsquo;t use
                    </label>
                  )}
                </div>
              ))}
            </section>
          </>
        )}

        <div className="flex items-center justify-between">
          <Button type="button" size="sm" variant="outline" onClick={() => void identifyScreens().catch((e) => onError(String(e)))}>
            Identify
          </Button>
          <Button ref={doneRef} type="button" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The displays drawn where the system has them, numbered as Identify shows them. */
function ScreenMap({ screens }: { screens: ScreenInfo[] }) {
  if (screens.length === 0) return null;
  const minX = Math.min(...screens.map((s) => s.x));
  const minY = Math.min(...screens.map((s) => s.y));
  const maxX = Math.max(...screens.map((s) => s.x + s.width));
  const maxY = Math.max(...screens.map((s) => s.y + s.height));
  const scale = Math.min(MAP_W / (maxX - minX), MAP_H / (maxY - minY));
  const w = (maxX - minX) * scale;
  const h = (maxY - minY) * scale;
  return (
    <div className="flex justify-center rounded-md bg-muted/40 p-3">
      <div className="relative" style={{ width: w, height: h }} data-testid="screen-map">
        {screens.map((s, i) => (
          <div
            key={s.key}
            className={cn(
              "absolute flex flex-col items-center justify-center gap-0.5 overflow-hidden rounded border p-1 text-center",
              s.operator ? "border-primary bg-primary/10" : "border-border bg-background",
              s.avoid && "border-dashed opacity-60",
            )}
            style={{
              left: (s.x - minX) * scale + 1,
              top: (s.y - minY) * scale + 1,
              width: s.width * scale - 2,
              height: s.height * scale - 2,
            }}
            title={`${displayName(s, i)} (${sizeLabel(s)})`}
          >
            <span className="text-lg font-semibold leading-none">{i + 1}</span>
            {s.operator && <span className="text-[10px] text-muted-foreground">This screen</span>}
            {s.avoid && <span className="text-[10px] text-muted-foreground">Don&rsquo;t use</span>}
            {s.windows.map((l) => (
              <span key={l} className="max-w-full truncate rounded bg-sky-600 px-1 text-[10px] font-medium text-white">
                {windowName(l)}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The window Identify puts on each display: its number and name. */
export function IdentifyScreen() {
  const params = new URLSearchParams(window.location.search);
  const name = params.get("name") ?? "";
  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-2 bg-black text-white">
      <Monitor className="size-6 opacity-60" />
      <div className="text-8xl font-bold leading-none">{params.get("n") ?? "?"}</div>
      <div className="max-w-full truncate px-4 text-sm opacity-70">{/^Monitor #\d+$/.test(name) ? "" : name}</div>
    </div>
  );
}
