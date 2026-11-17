import { AppShell } from "@/components/AppShell";
import { DebugWindow } from "@/components/debug/DebugWindow";
import { AudienceView } from "@/components/presentation/AudienceView";
import { BandView } from "@/components/band/BandView";
import { IdentifyScreen } from "@/components/presentation/ScreensDialog";

/**
 * The audience projection window is created (in Rust) with the label
 * `audience`. Detection must be synchronous and happen before `AppShell`
 * mounts — otherwise the projection window would briefly spin up an operator
 * session. Tauri v2 exposes the current window's label synchronously via its
 * internals; in the plain browser preview (no Tauri) we fall back to the
 * `?window=audience` query param.
 */
function windowKind(): string {
  if (typeof window === "undefined") return "main";
  const label = (
    window as unknown as {
      __TAURI_INTERNALS__?: { metadata?: { currentWindow?: { label?: string } } };
    }
  ).__TAURI_INTERNALS__?.metadata?.currentWindow?.label;
  // Band screens opened on this computer's own monitors (`open_band_window`).
  if (label?.startsWith("band-")) return "band";
  // A display's number, shown by the Screens dialog's Identify.
  if (label?.startsWith("identify-")) return "identify";
  if (label) return label;
  // Band screens are served to phones and tablets by the band server at
  // `/band` and `/leader` (or `?window=band|leader` via the dev server).
  if (/^\/(band|leader)\b/.test(window.location.pathname)) return "band";
  // …and by the online relay at `/r/<room>/band` and `/r/<room>/leader`.
  if (/^\/r\/[a-z0-9]+\/(band|leader)$/.test(window.location.pathname)) return "band";
  const param = new URLSearchParams(window.location.search).get("window");
  if (param === "leader") return "band";
  return param ?? "main";
}

export default function App() {
  const kind = windowKind();
  if (kind === "audience") return <AudienceView />;
  // A musician's device: never the operator app, whatever else it can reach.
  if (kind === "band") return <BandView />;
  // The debug window must not mount `AppShell` either: a second operator
  // session would open its own recording sink and audio handlers.
  if (kind === "debug") return <DebugWindow />;
  if (kind === "identify") return <IdentifyScreen />;
  return <AppShell />;
}
