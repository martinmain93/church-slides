/**
 * The online room page of the band screens panel: set up the online relay
 * in the church's own Cloudflare account (once), then open a room.
 *
 * Reached from a quiet link in the panel (`BandScreensButton`): the local
 * network is the normal way and needs no internet. This is for churches
 * whose Wi-Fi keeps devices apart.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { QrCode } from "@/components/band/QrCode";
import type { OnlineRoomApi } from "@/lib/band/useOnlineRoom";
import {
  cloudRelayForget,
  cloudRelayHealth,
  cloudRelayOpenPage,
  cloudRelayRedeploy,
  cloudRelaySetupOauth,
  cloudRelaySetupToken,
  type CloudSetupOutcome,
} from "@/lib/tauri";

const SIGN_UP = "https://dash.cloudflare.com/sign-up";
const API_TOKENS = "https://dash.cloudflare.com/profile/api-tokens";

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function OnlineRoomPanel({
  online,
  autoStart = false,
  onClosed,
}: {
  online: OnlineRoomApi;
  /** Open the room as soon as it can be opened — the shortcut for when it's
   * set up. Off again once the operator has closed it. */
  autoStart?: boolean;
  /** The operator closed the room (so don't reopen it by itself). */
  onClosed?: () => void;
}) {
  const { status } = online;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [accounts, setAccounts] = useState<{ id: string; name: string }[] | null>(null);
  const [via, setVia] = useState<"oauth" | "token">("token");
  // A brand-new workers.dev address can take a minute or two to resolve.
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    if (!status?.configured || healthy) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      const ok = await cloudRelayHealth().catch(() => false);
      if (cancelled) return;
      setHealthy(ok);
      if (!ok) timer = setTimeout(check, 10_000);
    };
    void check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [status?.configured, healthy]);

  // The shortcut: set up, address answering, no room yet → open it.
  const canAutoStart = autoStart && Boolean(status?.configured) && healthy === true && online.room === null;
  useEffect(() => {
    if (canAutoStart) void online.start();
  }, [canAutoStart, online]);

  // "Make new links" asks twice: every QR code made so far stops working.
  const [confirmLinks, setConfirmLinks] = useState(false);
  useEffect(() => {
    if (!confirmLinks) return;
    const t = setTimeout(() => setConfirmLinks(false), 5000);
    return () => clearTimeout(t);
  }, [confirmLinks]);

  if (!status) return null;

  async function finish(outcome: CloudSetupOutcome, how: "oauth" | "token") {
    if (outcome.outcome === "choose_account") {
      setVia(how);
      setAccounts(outcome.accounts);
      return;
    }
    setAccounts(null);
    setToken("");
    setHealthy(null);
    await online.refreshStatus();
  }

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const linkButton = (label: string, url: string) => (
    <button type="button" className="underline underline-offset-2" onClick={() => void cloudRelayOpenPage(url)}>
      {label}
    </button>
  );

  return (
    <section className="flex flex-col gap-2 text-sm" data-testid="online-panel">

      {!status.relay_built ? (
        <p className="text-xs text-muted-foreground">This build doesn&rsquo;t include the online relay (run <code>pnpm build:relay</code>).</p>
      ) : !status.configured ? (
        <>
          <p className="text-xs text-muted-foreground">
            For when the church Wi-Fi won&rsquo;t let devices reach this computer. Band devices connect over the
            internet instead — any Wi-Fi, or mobile data. It runs on your church&rsquo;s own free Cloudflare account;
            this is a one-time setup.
          </p>
          {accounts ? (
            <label className="flex flex-col gap-1 text-xs">
              Which Cloudflare account should it go in?
              <select
                className="rounded-md border border-input bg-background px-2 py-1 text-sm"
                defaultValue=""
                onChange={(e) =>
                  e.target.value &&
                  void run("Setting up…", async () =>
                    finish(
                      via === "oauth" ? await cloudRelaySetupOauth(e.target.value) : await cloudRelaySetupToken(token, e.target.value),
                      via,
                    ),
                  )
                }
              >
                <option value="" disabled>
                  Choose…
                </option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              {status.oauth_available && (
                <Button
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => void run("Waiting for Cloudflare sign-in in your browser…", async () => finish(await cloudRelaySetupOauth(), "oauth"))}
                >
                  Sign in with Cloudflare
                </Button>
              )}
              <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
                <li>No Cloudflare account yet? {linkButton("Create one free", SIGN_UP)}.</li>
                <li>
                  Open {linkButton("API Tokens", API_TOKENS)} → <b>Create Token</b> → use the{" "}
                  <b>Edit Cloudflare Workers</b> template → <b>Create Token</b>.
                </li>
                <li>Paste the token here. It&rsquo;s kept in this computer&rsquo;s Keychain, only to set up and update the relay.</li>
              </ol>
              <div className="flex gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="Cloudflare API token"
                  className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 font-mono text-xs"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  data-testid="online-token"
                />
                <Button
                  size="sm"
                  variant={status.oauth_available ? "outline" : "default"}
                  disabled={busy !== null || token.trim().length < 20}
                  onClick={() => void run("Setting up your relay…", async () => finish(await cloudRelaySetupToken(token), "token"))}
                  data-testid="online-setup"
                >
                  Set up
                </Button>
              </div>
            </>
          )}
        </>
      ) : online.room === null ? (
        <>
          <p className="text-xs text-muted-foreground">
            Set up on <b>{status.account_name}</b>&rsquo;s Cloudflare account.
            {healthy === false && " It's still coming online — a new address can take a couple of minutes."}
          </p>
          <Button size="sm" disabled={busy !== null || healthy === false} onClick={() => void run("Starting…", online.start)} data-testid="online-start">
            Open an online room
          </Button>
          <div className="flex gap-3 text-xs text-muted-foreground">
            {status.needs_update && (
              <button
                type="button"
                className="underline"
                onClick={() => void run("Updating the relay…", async () => {
                  await cloudRelayRedeploy();
                  await online.refreshStatus();
                })}
              >
                Update the relay (this app has a newer one)
              </button>
            )}
            <button
              type="button"
              className="underline"
              onClick={() => void run("Disconnecting…", async () => {
                await cloudRelayForget();
                await online.refreshStatus();
              })}
              title="Forget the Cloudflare setup on this computer and remove its token from the Keychain. The relay stays in your Cloudflare account until you delete it there."
            >
              Disconnect Cloudflare
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="flex justify-around">
            <QrCode text={online.room.bandUrl} label="Band (online)" />
            <QrCode text={online.room.leaderUrl} label="Worship leader (online)" />
          </div>
          <div className="flex items-center justify-between text-xs">
            <span>
              {online.connection === "live"
                ? "Connected"
                : online.connection === "reconnecting"
                  ? "Reconnecting to the relay…"
                  : "Connecting…"}
            </span>
            <span className="text-muted-foreground">
              {online.clients} device{online.clients === 1 ? "" : "s"} online
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            Works on any Wi-Fi or mobile data. These links stay the same each time you open the room, so devices
            only need to scan once — a phone left on the page reconnects by itself.
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              onClosed?.();
              online.stop();
            }}
            data-testid="online-stop"
          >
            Close the online room
          </Button>
          <button
            type="button"
            className="self-start text-xs text-muted-foreground underline"
            onClick={() => {
              if (!confirmLinks) return setConfirmLinks(true);
              setConfirmLinks(false);
              void run("Making new links…", online.newLinks);
            }}
            title="For when a link has been shared further than it should: the old QR codes and bookmarks stop working"
            data-testid="online-new-links"
          >
            {confirmLinks ? "Tap again: the old QR codes will stop working" : "Make new links"}
          </button>
        </>
      )}

      {busy && <p className="text-xs">{busy}</p>}
      {(error ?? online.error) && <p className="text-xs text-destructive">{error ?? online.error}</p>}
    </section>
  );
}
