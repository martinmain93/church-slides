import { useCallback, useEffect, useMemo, useState } from "react";

import { BandView } from "@/components/band/BandView";
import type { MemberSource } from "@/lib/band/useBandConnection";
import {
  MemberSession,
  NotMemberError,
  pickOrganization,
  SignedOutError,
  startMemberSignIn,
  type Membership,
} from "@/lib/band/memberSignIn";

const LAST_ORG_KEY = "church-slides.band-member.org";

function rememberedOrg(): string | null {
  try {
    return localStorage.getItem(LAST_ORG_KEY);
  } catch {
    return null;
  }
}

function rememberOrg(orgId: string): void {
  try {
    localStorage.setItem(LAST_ORG_KEY, orgId);
  } catch {
    // Storage off: the person picks again next time.
  }
}

type State =
  | { kind: "loading" }
  | { kind: "signed-out"; error?: string }
  | { kind: "choose"; session: MemberSession; email: string | null; memberships: Membership[]; live: Record<string, boolean> }
  | { kind: "following"; session: MemberSession; org: Membership };

const shell = "flex h-dvh flex-col items-center justify-center gap-3 bg-background p-6 text-center text-foreground";
const button = "rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-muted";

/**
 * The website's band page with no band link: sign in, and follow the live set
 * of an organization you're in. With a service running in just one of them,
 * it goes straight there.
 */
export function MemberBandPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async (session: MemberSession) => {
    try {
      const { email, memberships } = await session.memberships();
      const live: Record<string, boolean> = {};
      await Promise.all(
        memberships.map(async (m) => {
          live[m.orgId] = await session.liveStatus(m.orgId).then(
            (s) => s.hostConnected,
            () => false,
          );
        }),
      );
      const org = pickOrganization(memberships, live, rememberedOrg());
      if (org) {
        rememberOrg(org.orgId);
        setState({ kind: "following", session, org });
      } else {
        setState({ kind: "choose", session, email, memberships, live });
      }
    } catch (err) {
      if (err instanceof SignedOutError) setState({ kind: "signed-out", error: err.message });
      else setState({ kind: "signed-out", error: "Couldn't reach Church Slides. Check this device is online." });
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const session = (await MemberSession.finishSignIn()) ?? MemberSession.stored();
        if (session) await load(session);
        else setState({ kind: "signed-out" });
      } catch (err) {
        setState({ kind: "signed-out", error: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, [load]);

  const signOut = useCallback(async (session: MemberSession) => {
    await session.signOut();
    setState({ kind: "signed-out" });
  }, []);

  const following = state.kind === "following" ? state : null;
  const source = useMemo<MemberSource | undefined>(() => {
    if (!following) return undefined;
    return {
      socketUrl: () =>
        following.session.liveSocketUrl(following.org.orgId).catch((err: unknown) => {
          // Signed out or removed: retrying won't help. Anything else
          // (offline, the server busy) is worth another go.
          if (err instanceof SignedOutError || err instanceof NotMemberError) throw Object.assign(err, { removed: true });
          throw err;
        }),
    };
  }, [following]);

  if (following && source) {
    return (
      <BandView
        member={{ source, orgName: following.org.orgName, onSignOut: () => void signOut(following.session) }}
      />
    );
  }

  if (state.kind === "loading") {
    return <div className={`${shell} text-muted-foreground`}>Signing in…</div>;
  }

  if (state.kind === "choose") {
    const { session, email, memberships, live } = state;
    return (
      <div className={shell} data-testid="choose-organization">
        {memberships.length === 0 ? (
          <>
            <div className="text-[1.2em]">You&apos;re not in an organization yet</div>
            <div className="max-w-sm text-sm text-muted-foreground">
              Ask whoever runs the slides to invite you{email ? ` (${email})` : ""}, or open your band link instead.
            </div>
          </>
        ) : (
          <>
            <div className="text-[1.2em]">Which organization?</div>
            <div className="flex w-full max-w-sm flex-col gap-2">
              {memberships.map((m) => (
                <button
                  key={m.orgId}
                  type="button"
                  className={`${button} flex items-center justify-between`}
                  onClick={() => {
                    rememberOrg(m.orgId);
                    setState({ kind: "following", session, org: m });
                  }}
                >
                  <span>{m.orgName}</span>
                  {live[m.orgId] && <span className="text-xs text-green-600 dark:text-green-400">Live now</span>}
                </button>
              ))}
            </div>
          </>
        )}
        <button type="button" className="text-sm text-muted-foreground underline" onClick={() => void signOut(session)}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className={shell} data-testid="no-band-link">
      <div className="text-[1.2em]">Follow the slides</div>
      <div className="max-w-sm text-sm text-muted-foreground">
        Sign in to follow your organization&apos;s service, with lyrics and chords. Or open the band link from whoever runs the
        slides.
      </div>
      <button type="button" className={button} onClick={() => void startMemberSignIn()} data-testid="member-sign-in">
        Sign in
      </button>
      {state.kind === "signed-out" && state.error && <div className="max-w-sm text-sm text-destructive">{state.error}</div>}
    </div>
  );
}
