import { useEffect, useState } from "react";

import { OrganizationTeam } from "@/components/settings/OrganizationTeam";
import { Button } from "@/components/ui/button";
import {
  acceptInvite,
  accountStatus,
  createOrganization,
  refreshAccount,
  roleLabel,
  signIn,
  signOut,
  signOutEverywhere,
  type AccountStatus,
} from "@/lib/account";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";

/** A one-line form that opens from a text button: "New organization",
 * "Join with an invite". */
function InlineForm({
  label,
  placeholder,
  submitLabel,
  busy,
  onSubmit,
}: {
  label: string;
  placeholder: string;
  submitLabel: string;
  busy: boolean;
  onSubmit: (value: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  if (!open) {
    return (
      <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setOpen(true)}>
        {label}
      </Button>
    );
  }
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value.trim()) return;
        void onSubmit(value.trim()).then((done) => {
          if (done) {
            setValue("");
            setOpen(false);
          }
        });
      }}
    >
      <input
        autoFocus
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm"
      />
      <Button type="submit" size="sm" disabled={busy || !value.trim()}>
        {submitLabel}
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </form>
  );
}

/**
 * Settings → Account: sign in to a Church Slides server, or out, and the
 * organizations you're in. Optional: nothing else in the app depends on it.
 */
export function AccountSettings() {
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState<null | "in" | "out" | "everywhere" | "org">(null);
  const [openOrg, setOpenOrg] = useState<string | null>(null);

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

  /** Run a team action, then reload the account so the list is current. */
  async function orgAction<T>(action: () => Promise<T>, success?: (result: T) => string): Promise<boolean> {
    setBusy("org");
    try {
      const result = await action();
      if (success) toast.success(success(result));
      setStatus(await refreshAccount());
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const joinForm = (
    <InlineForm
      label="Join with an invite"
      placeholder="Paste the invite link"
      submitLabel="Join"
      busy={busy !== null}
      onSubmit={async (link) => {
        if (!status?.signedIn) {
          // Sign in through the invite: no pilot passcode needed, and the
          // server adds them as they sign in.
          setBusy("in");
          try {
            setStatus(await signIn(link));
            return true;
          } catch (err) {
            toast.error(err instanceof Error ? err.message : String(err));
            return false;
          } finally {
            setBusy(null);
          }
        }
        return orgAction(
          () => acceptInvite(link),
          (r) =>
            r.joined
              ? `Joined ${r.membership.orgName} as ${roleLabel(r.membership.role).toLowerCase()}.`
              : `You're already in ${r.membership.orgName}.`,
        );
      }}
    />
  );

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
          <Button type="button" size="sm" disabled={busy !== null} onClick={() => void run("in", () => signIn())}>
            {busy === "in" ? "Waiting…" : "Sign in"}
          </Button>
        </div>
        {joinForm}
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
        <ul className="space-y-2 text-sm">
          {status.memberships.map((m) => (
            <li key={m.orgId} className="space-y-2">
              <div className="flex items-center justify-between gap-4">
                <span>{m.orgName}</span>
                <span className="flex items-center gap-2">
                  <span className="text-muted-foreground">{roleLabel(m.role)}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-expanded={openOrg === m.orgId}
                    onClick={() => setOpenOrg(openOrg === m.orgId ? null : m.orgId)}
                  >
                    Team
                  </Button>
                </span>
              </div>
              {openOrg === m.orgId && (
                <OrganizationTeam
                  membership={m}
                  myUserId={status.userId}
                  onLeft={() => {
                    setOpenOrg(null);
                    void refreshAccount().then(setStatus, () => {});
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-1">
        <InlineForm
          label="New organization"
          placeholder="Name, e.g. Grace Chapel"
          submitLabel="Create"
          busy={busy !== null}
          onSubmit={(name) => orgAction(() => createOrganization(name))}
        />
        {joinForm}
      </div>
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
