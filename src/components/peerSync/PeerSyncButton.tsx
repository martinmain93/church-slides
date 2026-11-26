/**
 * Operator control for moving setlists between laptops on the same Wi-Fi.
 * Lists the other Church Slides operators found on the network and offers,
 * for each, "Send this service" and "Get a service". The panel opens by
 * itself the first time a new operator appears, so nobody has to know it's
 * there; it never takes over the screen (the service may be running).
 *
 * Without a setlist (Home) it only gets one — the computer that needs a
 * setlist is usually the one that doesn't have any yet.
 *
 * The whole song library moves the same way: "Send my whole library" on a
 * computer, or "Their whole library" at the top of the other's setlists.
 */

import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowUpFromLine, Laptop, Loader2, Radio } from "lucide-react";

import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/InfoTip";
import { SyncReviewDialog } from "@/components/peerSync/SyncReviewDialog";
import { summaryMessage } from "@/components/peerSync/IncomingSync";
import { usePeers } from "@/lib/peerSync/usePeers";
import {
  addPeerByAddress,
  fetchPeerLibrary,
  fetchPeerSetlist,
  listPeerSetlists,
  peerAccessStatus,
  peerHasAccess,
  peerOfferStatus,
  requestPeerAccess,
  sendLibraryToPeer,
  sendSetlistToPeer,
  type Peer,
  type PeerRequestStatus,
  type RemoteSetlist,
  type SetlistBundle,
} from "@/lib/tauri";
import { cn } from "@/lib/utils";

type Flow =
  | { step: "idle" }
  | { step: "sending"; peer: Peer; offerId: string; status: PeerRequestStatus; what: string }
  | { step: "asking"; peer: Peer; requestId: string; status: PeerRequestStatus }
  | { step: "picking"; peer: Peer; setlists: RemoteSetlist[] | null }
  | { step: "review"; peer: Peer; bundle: SetlistBundle };

/** Peers already announced this session — the panel pops open once per peer, not per mount. */
const announced = new Set<string>();

const POLL_MS = 1500;

/** Said wherever a whole library is about to be copied. */
export const COPYRIGHT_NOTE = "Song words are covered by your church's CCLI licence — only copy them to your church's own computers.";

export function PeerSyncButton({
  setlistId,
  setlistName,
  onMessage,
  openSignal = 0,
  hideWhenAlone = false,
  align = "left",
  onImported,
}: {
  /** The setlist "Send" sends; without one the panel only gets setlists. */
  setlistId?: string;
  setlistName?: string;
  onMessage: (message: string, kind?: "success" | "error") => void;
  /** After a setlist from another computer is in the library. */
  onImported?: () => void;
  /** Bump to open the panel from elsewhere — a "⋯" menu item. */
  openSignal?: number;
  /** Show the button only while another operator is on the network; the
   * panel still opens by itself when one appears, and from the "⋯" menu. */
  hideWhenAlone?: boolean;
  /** Which edge of the button the panel lines up with. */
  align?: "left" | "right";
}) {
  const { peers, me, refreshMe } = usePeers();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (openSignal > 0) setOpen(true);
  }, [openSignal]);
  useEffect(() => {
    if (open) refreshMe();
  }, [open, refreshMe]);
  const [fresh, setFresh] = useState<string | null>(null);
  const [flow, setFlow] = useState<Flow>({ step: "idle" });
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // The operator view re-renders on every slide; keep the poll timer off that clock.
  const say = useRef({ onMessage });
  say.current = { onMessage };

  // A new operator on the network: say so.
  useEffect(() => {
    const newcomer = peers.find((p) => !p.manual && !announced.has(p.id));
    for (const p of peers) announced.add(p.id);
    if (newcomer) {
      setFresh(newcomer.id);
      setOpen(true);
    }
  }, [peers]);

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

  // Wait for the other operator to answer: one check per pending state, and
  // each answer is a new state, so this re-arms until the answer is final.
  useEffect(() => {
    if (flow.step !== "sending" && flow.step !== "asking") return;
    if (flow.status !== "pending") return;
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        if (flow.step === "sending") {
          const status = await peerOfferStatus(flow.peer.id, flow.offerId);
          if (cancelled) return;
          setFlow({ ...flow, status });
          if (status === "accepted") say.current.onMessage(`${flow.peer.name} imported ${flow.what}.`);
        } else {
          const status = await peerAccessStatus(flow.peer.id, flow.requestId);
          if (cancelled) return;
          setFlow(status === "accepted" ? { step: "picking", peer: flow.peer, setlists: null } : { ...flow, status });
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setFlow({ step: "idle" });
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [flow]);

  // Once allowed, list what the other computer has.
  useEffect(() => {
    if (flow.step !== "picking" || flow.setlists) return;
    listPeerSetlists(flow.peer.id)
      .then((setlists) => setFlow((f) => (f.step === "picking" ? { ...f, setlists } : f)))
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setFlow({ step: "idle" });
      });
  }, [flow]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const send = (peer: Peer, id: string) =>
    run(`send:${peer.id}`, async () => {
      const offerId = await sendSetlistToPeer(peer.id, id);
      setFlow({ step: "sending", peer, offerId, status: "pending", what: `“${setlistName ?? "the service"}”` });
    });

  const sendLibrary = (peer: Peer) =>
    run(`library:${peer.id}`, async () => {
      const offerId = await sendLibraryToPeer(peer.id);
      setFlow({ step: "sending", peer, offerId, status: "pending", what: "your library" });
    });

  const pickLibrary = (peer: Peer) =>
    run("pick:library", async () => {
      const bundle = await fetchPeerLibrary(peer.id);
      setFlow({ step: "review", peer, bundle });
      setOpen(false);
    });

  const get = (peer: Peer) =>
    run(`get:${peer.id}`, async () => {
      if (await peerHasAccess(peer.id)) {
        setFlow({ step: "picking", peer, setlists: null });
        return;
      }
      const requestId = await requestPeerAccess(peer.id);
      setFlow({ step: "asking", peer, requestId, status: "pending" });
    });

  const pick = (peer: Peer, s: RemoteSetlist) =>
    run(`pick:${s.id}`, async () => {
      const bundle = await fetchPeerSetlist(peer.id, s.id);
      setFlow({ step: "review", peer, bundle });
      setOpen(false);
    });

  const addManual = () =>
    run("manual", async () => {
      await addPeerByAddress(address);
      setAddress("");
    });

  return (
    <div className="relative" ref={panelRef}>
      {(!hideWhenAlone || peers.length > 0) && (
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1"
          onClick={() => setOpen((v) => !v)}
          title={
            setlistId
              ? "Send or get a service from another Church Slides computer on this network"
              : "Get a service from another Church Slides computer on this network"
          }
          data-testid="peer-sync-button"
        >
          <Radio className="size-4" />
          {setlistId ? "Nearby" : "From another computer"}
          {peers.length > 0 && (
            <span className="ml-1 rounded-full bg-sky-600 px-1.5 text-[10px] font-semibold text-white">{peers.length}</span>
          )}
        </Button>
      )}

      {open && (
        <div
          className={cn(
            "absolute top-10 z-50 w-[26rem] rounded-lg border border-border bg-background p-4 text-sm text-foreground shadow-xl",
            align === "right" ? "right-0" : "left-0",
          )}
          data-testid="peer-sync-panel"
        >
          {flow.step === "picking" ? (
            <div className="flex flex-col gap-2">
              <div className="font-medium">Services on {flow.peer.name}</div>
              <button
                type="button"
                className="flex w-full items-baseline gap-2 rounded border border-border px-3 py-2 text-left hover:bg-muted disabled:opacity-50"
                disabled={busy !== null}
                onClick={() => void pickLibrary(flow.peer)}
                data-testid="peer-get-library"
              >
                <span className="min-w-0 flex-1 font-medium">Their whole library</span>
                <span className="shrink-0 text-xs text-muted-foreground">every song, with chords</span>
                {busy === "pick:library" && <Loader2 className="size-3 animate-spin" />}
              </button>
              <p className="text-xs text-muted-foreground">{COPYRIGHT_NOTE}</p>
              {!flow.setlists ? (
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Loading…
                </div>
              ) : flow.setlists.length === 0 ? (
                <div className="text-muted-foreground">No services there yet.</div>
              ) : (
                <ul className="max-h-72 divide-y divide-border overflow-y-auto rounded border border-border">
                  {flow.setlists.map((s) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        className="flex w-full items-baseline gap-2 px-3 py-2 text-left hover:bg-muted disabled:opacity-50"
                        disabled={busy !== null}
                        onClick={() => void pick(flow.peer, s)}
                      >
                        <span className="min-w-0 flex-1 truncate font-medium">{s.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {s.song_count} song{s.song_count === 1 ? "" : "s"}
                        </span>
                        {busy === `pick:${s.id}` && <Loader2 className="size-3 animate-spin" />}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <Button variant="ghost" size="sm" className="self-start" onClick={() => setFlow({ step: "idle" })}>
                Back
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {me?.network_code && (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="peer-network">
                  Network <span className="font-mono font-semibold text-foreground">{me.network_code}</span> — the other computer
                  should show the same.
                  <InfoTip label="About the network code" align="right">
                    The last four characters of this Wi-Fi router&apos;s address. Computers only find each other on the same
                    network, so if the other computer shows a different code, it&apos;s on a different Wi-Fi — join the same one on
                    both.
                  </InfoTip>
                </p>
              )}
              {peers.length === 0 ? (
                <p className="text-muted-foreground">
                  No other Church Slides computers found on this network yet. They appear here by themselves when the app is open on
                  the same Wi-Fi.
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {fresh && peers.some((p) => p.id === fresh) && (
                    <li className="text-xs font-medium text-sky-500">Another operator is on this network.</li>
                  )}
                  {peers.map((p) => (
                    <li key={p.id} className={cn("rounded-md border p-3", p.id === fresh ? "border-sky-500" : "border-border")}>
                      <div className="flex items-center gap-2 font-medium">
                        <Laptop className="size-4 text-muted-foreground" />
                        {p.name}
                      </div>
                      <div className="mt-2 flex gap-2">
                        {setlistId && (
                          <Button size="sm" className="h-8 flex-1 gap-1" disabled={busy !== null || flow.step !== "idle"} onClick={() => void send(p, setlistId)}>
                            <ArrowUpFromLine className="size-4" />
                            Send this service
                          </Button>
                        )}
                        <Button
                          variant={setlistId ? "outline" : "default"}
                          size="sm"
                          className="h-8 flex-1 gap-1"
                          disabled={busy !== null || flow.step !== "idle"}
                          onClick={() => void get(p)}
                        >
                          <ArrowDownToLine className="size-4" />
                          Get a service
                        </Button>
                      </div>
                      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <button
                          type="button"
                          className="underline disabled:opacity-50"
                          disabled={busy !== null || flow.step !== "idle"}
                          onClick={() => void sendLibrary(p)}
                          data-testid="peer-send-library"
                        >
                          Send my whole library
                        </button>
                        {busy === `library:${p.id}` && <Loader2 className="size-3 animate-spin" />}
                        <InfoTip label="About sending the library">
                          Every song, with its chords, so {p.name} has the same library. They review it before anything is saved.{" "}
                          {COPYRIGHT_NOTE}
                        </InfoTip>
                      </div>
                      <FlowStatus flow={flow} peer={p} onDone={() => setFlow({ step: "idle" })} />
                    </li>
                  ))}
                </ul>
              )}

              {error && <p className="text-destructive">{error}</p>}

              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">Other computer not showing up?</summary>
                <div className="mt-2 flex flex-col gap-2">
                  <p>
                    Some church and guest networks stop computers from finding each other. Type the other computer's address — it's
                    shown in this same panel on that computer.
                  </p>
                  <form
                    className="flex gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void addManual();
                    }}
                  >
                    <input
                      value={address}
                      onChange={(e) => setAddress(e.target.value)}
                      placeholder="192.168.1.20"
                      aria-label="Other computer's address"
                      className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 font-mono text-sm text-foreground"
                    />
                    <Button type="submit" size="sm" variant="outline" className="h-8" disabled={!address.trim() || busy !== null}>
                      Connect
                    </Button>
                  </form>
                  {me && (
                    <p>
                      This computer: <span className="font-medium text-foreground">{me.name}</span>
                      {me.addresses.length > 0 && (
                        <>
                          {" "}
                          at <span className="font-mono text-foreground">{me.addresses[0]}</span>
                        </>
                      )}
                    </p>
                  )}
                  {me?.discovery_error && <p>Automatic discovery is off on this computer: {me.discovery_error}</p>}
                </div>
              </details>
            </div>
          )}
        </div>
      )}

      {flow.step === "review" && (
        <SyncReviewDialog
          bundle={flow.bundle}
          fromName={flow.peer.name}
          onCancel={() => setFlow({ step: "idle" })}
          onImported={(summary) => {
            setFlow({ step: "idle" });
            onMessage(summaryMessage(summary));
            onImported?.();
          }}
        />
      )}
    </div>
  );
}

function FlowStatus({ flow, peer, onDone }: { flow: Flow; peer: Peer; onDone: () => void }) {
  if ((flow.step !== "sending" && flow.step !== "asking") || flow.peer.id !== peer.id) return null;
  const text =
    flow.step === "sending"
      ? {
          pending: `Sent ${flow.what}. Waiting for ${peer.name} to review it…`,
          accepted: `${peer.name} imported it.`,
          declined: `${peer.name} declined it.`,
          expired: "That transfer expired. Send it again.",
        }[flow.status]
      : {
          pending: `Asked ${peer.name} for access — someone there needs to press Allow.`,
          accepted: "Allowed.",
          declined: `${peer.name} didn't allow it.`,
          expired: "The request expired. Ask again.",
        }[flow.status];
  return (
    <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground" data-testid="peer-flow-status">
      {flow.status === "pending" && <Loader2 className="size-3 animate-spin" />}
      <span className="flex-1">{text}</span>
      <button type="button" className="underline" onClick={onDone}>
        {flow.status === "pending" ? "Cancel" : "OK"}
      </button>
    </div>
  );
}
