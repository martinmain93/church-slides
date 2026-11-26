/**
 * The web app: plan services and look after the organization's songs from
 * any device. Presenting stays on the slides computer
 * (docs/roadmap/WEB_AND_MOBILE.md).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CalendarDays, Check, Music, UserRound } from "lucide-react";

import { NotMemberError, SignedOutError, type Membership } from "@/lib/account/browserSignIn";
import { LibraryProvider } from "@/library/context";
import { webLibrary, type WebLibrary } from "@/library/web";
import { browserPersist, clearAllLibraries, libraryDbName, type Persist } from "@/library/web/persist";
import { RecordStore } from "@/library/web/store";
import { LibrarySync, type SyncStatus } from "@/library/web/sync";
import { href, useRoute, type Route } from "@/web/route";
import { rolePermissions, startWebSignIn, WebSession } from "@/web/session";
import { ServicePage } from "@/web/pages/ServicePage";
import { ServicesPage } from "@/web/pages/ServicesPage";
import { SongEditor } from "@/web/pages/SongEditor";
import { SongPage } from "@/web/pages/SongPage";
import { SongsPage } from "@/web/pages/SongsPage";
import { ConfirmSheet, Empty, PageBoundary, PageHeader, PrimaryButton, QuietButton, useTitle } from "@/web/ui";
import { cn } from "@/lib/utils";
import { roleLabel } from "@/core/roles";

const ORG_KEY = "church-slides.web.org";

function remembered(): string | null {
  try {
    return localStorage.getItem(ORG_KEY);
  } catch {
    return null;
  }
}

function remember(orgId: string | null): void {
  try {
    if (orgId) localStorage.setItem(ORG_KEY, orgId);
    else localStorage.removeItem(ORG_KEY);
  } catch {
    // Storage off: pick again next time.
  }
}

interface SignedIn {
  kind: "signed-in";
  session: WebSession;
  userId: string;
  email: string | null;
  memberships: Membership[];
  org: Membership | null;
}

type State = { kind: "loading" } | { kind: "signing-out" } | { kind: "signed-out"; error?: string } | SignedIn;

const centred = "web-app flex min-h-dvh flex-col items-center justify-center gap-4 py-6 text-center [--web-pad-x:1.5rem]";

/** `persist` is for tests: the library kept in memory instead of IndexedDB. */
export function WebApp({ persist }: { persist?: (name: string) => Promise<Persist> } = {}) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async (session: WebSession) => {
    try {
      const { id, email, memberships } = await session.me();
      const pick = memberships.length === 1 ? memberships[0]! : (memberships.find((m) => m.orgId === remembered()) ?? null);
      setState({ kind: "signed-in", session, userId: id, email, memberships, org: pick });
    } catch (err) {
      setState({
        kind: "signed-out",
        error: err instanceof SignedOutError ? err.message : "Couldn't reach Church Slides. Check you're online, then reload.",
      });
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const session = (await WebSession.finishSignIn()) ?? WebSession.stored();
        if (session) await load(session);
        else setState({ kind: "signed-out" });
      } catch (err) {
        setState({ kind: "signed-out", error: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, [load]);

  const signOut = useCallback(async (s: SignedIn) => {
    // Close the library first (unmounting it stops sync and lets go of the
    // database), so deleting it doesn't wait on this page.
    setState({ kind: "signing-out" });
    await s.session.signOut();
    remember(null);
    await clearAllLibraries(s.memberships.map((m) => libraryDbName(s.session.server, m.orgId)));
    setState({ kind: "signed-out" });
  }, []);

  // Pages name the tab while signed in; every other screen is just the app.
  useEffect(() => {
    if (state.kind !== "signed-in") document.title = "Church Slides";
  }, [state.kind]);

  if (state.kind === "loading") return <div className={`${centred} text-muted-foreground`}>Opening…</div>;
  if (state.kind === "signing-out") return <div className={`${centred} text-muted-foreground`}>Signing out…</div>;

  if (state.kind === "signed-out") {
    return (
      <div className={centred} data-testid="web-signed-out">
        <img src="/logo.svg" alt="" className="size-14" />
        <h1 className="text-2xl font-semibold">Church Slides</h1>
        <p className="w-full max-w-sm text-sm leading-relaxed text-muted-foreground">
          Plan services and keep your team&apos;s songs up to date from any device. They&apos;re ready on the slides computer on Sunday.
        </p>
        <PrimaryButton className="min-w-40" onClick={() => void startWebSignIn()} data-testid="web-sign-in">
          Sign in
        </PrimaryButton>
        {state.error && <p className="w-full max-w-sm text-sm text-red-600 dark:text-red-400">{state.error}</p>}
      </div>
    );
  }

  const { org, memberships, email } = state;
  if (!org) {
    return (
      <OrgPicker
        email={email}
        memberships={memberships}
        current={remembered()}
        onPick={(m) => {
          remember(m.orgId);
          setState({ ...state, org: m });
        }}
        onCheckAgain={() => void load(state.session)}
        onSignOut={() => void signOut(state)}
      />
    );
  }

  return (
    <OrgLibrary
      key={`${org.orgId}/${org.role}`}
      session={state.session}
      userId={state.userId}
      org={org}
      persist={persist}
      account={(waiting) => (
        <AccountPage
          email={email}
          org={org}
          memberships={memberships}
          waiting={waiting}
          onSwitch={() => setState({ ...state, org: null })}
          onSignOut={() => void signOut(state)}
        />
      )}
      onRemoved={() => void load(state.session)}
    />
  );
}

function OrgPicker({
  email,
  memberships,
  current,
  onPick,
  onCheckAgain,
  onSignOut,
}: {
  email: string | null;
  memberships: Membership[];
  current: string | null;
  onPick: (m: Membership) => void;
  /** Read the memberships again (just accepted an invite). */
  onCheckAgain: () => void;
  onSignOut: () => void;
}) {
  const back = memberships.find((m) => m.orgId === current);
  return (
    <div className={centred} data-testid="web-choose-org">
      {memberships.length === 0 ? (
        <>
          <h1 className="text-xl font-semibold">You&apos;re not in an organization yet</h1>
          <p className="w-full max-w-sm text-sm leading-relaxed text-muted-foreground">
            Ask whoever runs your slides to invite you{email ? ` (${email})` : ""}, then open the link they send. Or create one in the
            Church Slides app under Settings → Account.
          </p>
          <PrimaryButton className="min-w-40" onClick={onCheckAgain}>
            Check again
          </PrimaryButton>
        </>
      ) : (
        <>
          <h1 className="text-xl font-semibold">Which organization?</h1>
          <div className="flex w-full max-w-sm flex-col gap-2">
            {memberships.map((m) => (
              <QuietButton key={m.orgId} className="h-12 justify-between" onClick={() => onPick(m)} aria-current={m.orgId === current || undefined}>
                <span className="truncate">{m.orgName}</span>
                {m.orgId === current && <Check className="size-4 shrink-0 text-muted-foreground" aria-label="Current" />}
              </QuietButton>
            ))}
            {back && (
              <button type="button" className="h-11 text-sm text-muted-foreground hover:text-foreground" onClick={() => onPick(back)}>
                Cancel
              </button>
            )}
          </div>
        </>
      )}
      <button type="button" className="h-11 px-3 text-sm text-muted-foreground underline hover:text-foreground" onClick={onSignOut}>
        Sign out
      </button>
    </div>
  );
}

/** One organization's library, open and syncing. */
function OrgLibrary({
  session,
  userId,
  org,
  persist,
  account,
  onRemoved,
}: {
  session: WebSession;
  userId: string;
  org: Membership;
  persist?: (name: string) => Promise<Persist>;
  /** The Account page, told how many changes haven't gone up yet. */
  account: (waiting: number) => ReactNode;
  onRemoved: () => void;
}) {
  const [opened, setOpened] = useState<{ lib: WebLibrary; sync: LibrarySync } | null>(null);
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const removed = useRef(onRemoved);
  removed.current = onRemoved;
  const { orgId, orgName } = org;

  useEffect(() => {
    let sync: LibrarySync | null = null;
    let store: RecordStore | null = null;
    let offStatus: (() => void) | null = null;
    let stopped = false;
    let toldRemoved = false;
    void (async () => {
      const name = libraryDbName(session.server, orgId);
      store = await RecordStore.open(await (persist ?? browserPersist)(name), userId);
      if (stopped) {
        store.close();
        return;
      }
      const api = <T,>(method: string, path: string, body?: unknown) =>
        session.api<T>(method, path, body).catch((err: unknown) => {
          // Once: the membership is read again, and this library closes.
          if (err instanceof NotMemberError && !toldRemoved) {
            toldRemoved = true;
            removed.current();
          }
          throw err;
        });
      sync = new LibrarySync(store, api, orgId, orgName, session.server);
      const lib = webLibrary(store, { changedHere: () => sync!.changedHere(), onChange: (l) => sync!.onChange(l) });
      setStatus(sync.getStatus());
      offStatus = sync.onStatus(setStatus);
      sync.start();
      setOpened({ lib, sync });
    })();
    return () => {
      stopped = true;
      offStatus?.();
      sync?.stop();
      store?.close();
    };
  }, [session, userId, orgId, orgName, persist]);

  const route = useRoute();
  const perms = rolePermissions(org.role);

  if (!opened || !status) return <div className={`${centred} text-muted-foreground`}>Opening {org.orgName}…</div>;
  if (!opened.lib.store.downloaded) {
    if (status.state === "loading") return <div className={`${centred} text-muted-foreground`}>Downloading {org.orgName}&apos;s library…</div>;
    const signedOut = status.state === "signed_out";
    return (
      <div className={centred}>
        <p className="w-full max-w-sm text-sm text-muted-foreground">
          {status.state === "offline"
            ? `You're offline. Connect once to download ${org.orgName}'s library.`
            : signedOut
              ? "You've been signed out. Reload to sign in again."
              : (status.message ?? `Couldn't download ${org.orgName}'s library.`)}
        </p>
        <QuietButton onClick={() => (signedOut ? window.location.reload() : void opened.sync.syncNow())}>{signedOut ? "Reload" : "Try again"}</QuietButton>
      </div>
    );
  }

  return (
    <LibraryProvider source={opened.lib}>
      <div className="web-app min-h-dvh lg:flex">
        <TabBar route={route} orgName={org.orgName} />
        <div className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col pb-[calc(3.5rem+env(safe-area-inset-bottom))] lg:max-w-3xl lg:pb-0">
          <SyncNotice status={status} onRetry={() => void opened.sync.syncNow()} />
          <main className="flex-1">
            <PageBoundary resetKey={href(route)}>{page(route, perms, account(status.waiting))}</PageBoundary>
          </main>
        </div>
      </div>
    </LibraryProvider>
  );
}

function page(route: Route, perms: { editSongs: boolean; editServices: boolean }, account: ReactNode): ReactNode {
  switch (route.page) {
    case "songs":
      return <SongsPage canEdit={perms.editSongs} />;
    case "song":
      return (
        <SongPage
          id={route.id}
          playKey={route.key}
          canEdit={perms.editSongs}
          back={route.from ? href({ page: "service", id: route.from }) : href({ page: "songs" })}
          from={route.from ?? null}
          itemId={route.item ?? null}
        />
      );
    case "edit-song":
      return perms.editSongs ? <SongEditor id={route.id} /> : <Empty>Your role can&apos;t edit songs.</Empty>;
    case "services":
      return <ServicesPage canEdit={perms.editServices} />;
    case "service":
      return <ServicePage id={route.id} canEdit={perms.editServices} created={!!route.created} copied={!!route.copied} />;
    case "account":
      return account;
  }
}

/** Only when something's waiting or wrong: there's no Sync button. */
function SyncNotice({ status, onRetry }: { status: SyncStatus; onRetry: () => void }) {
  let text: string | null = null;
  if (status.state === "offline") {
    // The device can be online while the server isn't reachable: say which.
    const where = typeof navigator !== "undefined" && navigator.onLine === false ? "Offline." : "Can't reach Church Slides.";
    text =
      status.waiting > 0
        ? `${where} ${status.waiting} change${status.waiting === 1 ? "" : "s"} kept here, waiting to go up.`
        : `${where} Showing the library as it was.`;
  } else if (status.state === "signed_out") text = "You've been signed out. Reload to sign in again.";
  else if (status.state === "error" || status.message) text = status.message ?? "Couldn't sync.";
  if (!text) return null;
  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted px-4 py-1.5 text-sm" role="status" data-testid="sync-notice">
      <span className="flex-1 py-1.5">{text}</span>
      {status.state !== "ok" && (
        <button
          type="button"
          className="-mr-2 h-11 shrink-0 px-2 underline"
          onClick={status.state === "signed_out" ? () => window.location.reload() : onRetry}
        >
          {status.state === "signed_out" ? "Reload" : "Try again"}
        </button>
      )}
    </div>
  );
}

/** Three tabs: along the bottom on a phone, down the side on a big screen. */
function TabBar({ route, orgName }: { route: Route; orgName: string }) {
  const inService = route.page === "service" || (route.page === "song" && !!route.from);
  const tabs = useMemo(
    () => [
      { to: href({ page: "services" }), label: "Services", icon: CalendarDays, on: route.page === "services" || inService },
      {
        to: href({ page: "songs" }),
        label: "Songs",
        icon: Music,
        on: !inService && (route.page === "songs" || route.page === "song" || route.page === "edit-song"),
      },
      { to: href({ page: "account" }), label: "Account", icon: UserRound, on: route.page === "account" },
    ],
    [route.page, inService],
  );
  return (
    <nav
      className={cn(
        "fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background pb-[env(safe-area-inset-bottom)]",
        "lg:sticky lg:top-0 lg:h-dvh lg:w-56 lg:shrink-0 lg:border-r lg:border-t-0 lg:pb-0",
      )}
      aria-label="Main"
    >
      <div className="hidden items-center gap-2.5 px-5 pb-4 pt-5 lg:flex">
        <img src="/logo.svg" alt="" className="size-7" />
        <div className="min-w-0">
          <div className="text-sm font-semibold leading-tight">Church Slides</div>
          <div className="truncate text-xs text-muted-foreground">{orgName}</div>
        </div>
      </div>
      <div className="mx-auto flex max-w-2xl lg:mx-0 lg:flex-col lg:gap-0.5 lg:px-3">
        {tabs.map((t) => (
          <a
            key={t.label}
            href={t.to}
            className={cn(
              "flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-xs",
              "lg:h-11 lg:flex-none lg:flex-row lg:justify-start lg:gap-3 lg:rounded-md lg:px-3 lg:text-sm",
              t.on ? "text-foreground lg:bg-muted" : "text-muted-foreground hover:text-foreground",
            )}
            aria-current={t.on ? "page" : undefined}
          >
            <t.icon className="size-5" />
            {t.label}
          </a>
        ))}
      </div>
    </nav>
  );
}


function AccountPage({
  email,
  org,
  memberships,
  waiting,
  onSwitch,
  onSignOut,
}: {
  email: string | null;
  org: Membership;
  memberships: Membership[];
  /** Changes made here that haven't gone up: signing out would lose them. */
  waiting: number;
  onSwitch: () => void;
  onSignOut: () => void;
}) {
  const perms = rolePermissions(org.role);
  const [asking, setAsking] = useState(false);
  useTitle("Account");
  return (
    <>
      <PageHeader title="Account" />
      <div className="space-y-6 px-4 py-5">
        <div>
          <div className="text-sm text-muted-foreground">Signed in as</div>
          <div className="font-medium">{email ?? "—"}</div>
        </div>
        <div>
          <div className="text-sm text-muted-foreground">Organization</div>
          <div className="font-medium">
            {org.orgName} · {roleLabel(org.role)}
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            {perms.editSongs ? "You can edit songs and services." : perms.editServices ? "You can plan services. Songs are read-only." : "You can see songs and services."}
          </div>
          {memberships.length > 1 && (
            <QuietButton className="mt-3" onClick={onSwitch}>
              Switch organization
            </QuietButton>
          )}
        </div>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Presenting, importing and chord editing are in the Church Slides app on the slides computer. Everything you change here shows up there.
        </p>
        <QuietButton className="w-full" onClick={() => (waiting > 0 ? setAsking(true) : onSignOut())} data-testid="web-sign-out">
          Sign out
        </QuietButton>
      </div>
      {asking && (
        <ConfirmSheet title="Sign out anyway?" action="Sign out" onClose={() => setAsking(false)} onAction={onSignOut}>
          {waiting === 1 ? "1 change" : `${waiting} changes`} made here {waiting === 1 ? "hasn't" : "haven't"} reached Church Slides yet, and would be lost. Connect
          first to keep {waiting === 1 ? "it" : "them"}.
        </ConfirmSheet>
      )}
    </>
  );
}
