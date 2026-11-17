import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  accountStatus,
  refreshAccount,
  roleLabel,
  signIn,
  signOut,
  signOutEverywhere,
  type AccountStatus,
} from "@/lib/account";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";

/**
 * Settings → Account: sign in to a Church Slides server, or out. Optional:
 * nothing else in the app depends on it.
 */
export function AccountSettings() {
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState<null | "in" | "out" | "everywhere">(null);

  useEffect(() => {
    let live = true;
    accountStatus()
      .then((s) => {
        if (!live) return;
        setStatus(s);
        // Then check with the server, quietly: it may have signed this
        // computer out, or added an organization.
        if (s.signedIn) void refreshAccount().then((fresh) => live && setStatus(fresh), () => {});
      })
      .catch(() => live && setUnavailable(true));
    return () => {
      live = false;
    };
  }, []);

  async function run(kind: "in" | "out" | "everywhere", action: () => Promise<AccountStatus>) {
    setBusy(kind);
    try {
      setStatus(await action());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function everywhere() {
    const ok = await confirm({
      title: "Sign out everywhere?",
      message: "Every computer, phone and app signed in to your account will be signed out, including this one.",
      confirmLabel: "Sign out everywhere",
      variant: "destructive",
    });
    if (ok) await run("everywhere", signOutEverywhere);
  }

  if (unavailable) return <div className="text-xs text-muted-foreground">Available in the desktop app.</div>;
  if (!status) return null;

  const server = status.serverIsDefault ? null : (
    <p className="text-xs text-muted-foreground">Server: {status.server}</p>
  );

  if (!status.signedIn) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {busy === "in"
              ? "Finish signing in in your browser…"
              : "Sign in to work with your team's library and live set."}
          </p>
          <Button type="button" size="sm" disabled={busy !== null} onClick={() => void run("in", signIn)}>
            {busy === "in" ? "Waiting…" : "Sign in"}
          </Button>
        </div>
        {server}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm">
          Signed in as <span className="font-medium">{status.displayName ?? status.email}</span>
        </p>
        {status.displayName && <p className="text-xs text-muted-foreground">{status.email}</p>}
      </div>
      {status.memberships.length === 0 ? (
        <p className="text-sm text-muted-foreground">You&apos;re not in an organization yet.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {status.memberships.map((m) => (
            <li key={m.orgId} className="flex justify-between gap-4">
              <span>{m.orgName}</span>
              <span className="text-muted-foreground">{roleLabel(m.role)}</span>
            </li>
          ))}
        </ul>
      )}
      {server}
      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" disabled={busy !== null} onClick={() => void run("out", signOut)}>
          Sign out
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={busy !== null} onClick={() => void everywhere()}>
          Sign out everywhere
        </Button>
      </div>
    </div>
  );
}
