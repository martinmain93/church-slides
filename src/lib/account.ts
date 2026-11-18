/**
 * Signing in to a Church Slides server (optional; docs/server-protocol/).
 * The Rust side does the OAuth flow, keeps the refresh token in the system
 * keychain, and talks to the server; this is the interface's view of it.
 */
import { invoke } from "@/lib/invoke";

export interface Membership {
  orgId: string;
  orgName: string;
  role: "owner" | "admin" | "set_leader" | "operator" | "member" | string;
}

export interface AccountStatus {
  signedIn: boolean;
  userId: string | null;
  email: string | null;
  displayName: string | null;
  memberships: Membership[];
  server: string;
  serverIsDefault: boolean;
}

/** What's known locally, instantly. */
export const accountStatus = () => invoke<AccountStatus>("account_status");
/** Ask the server; offline, returns what's known. */
export const refreshAccount = () => invoke<AccountStatus>("account_refresh");
/** Opens the system browser and waits (up to 5 minutes) for the reply. With
 * an invite link, a new person needs no pilot passcode and joins that
 * organization as they sign in. */
export const signIn = (invite?: string) => invoke<AccountStatus>("account_sign_in", { invite: invite ?? null });
export const signOut = () => invoke<AccountStatus>("account_sign_out");
export const signOutEverywhere = () => invoke<AccountStatus>("account_sign_out_everywhere");

const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  set_leader: "Set leader",
  operator: "Operator",
  member: "Member",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

// --- Organizations (signed in) -------------------------------------------------

export type Role = "owner" | "admin" | "set_leader" | "operator" | "member";
/** Roles an invite or a role change can give; ownership only moves by transfer. */
export const ASSIGNABLE_ROLES: Role[] = ["member", "operator", "set_leader", "admin"];

export interface Member {
  userId: string;
  displayName: string | null;
  /** Shown to owners and admins, and to each person for themselves. */
  email?: string;
  role: Role;
  joinedAt: number;
}

const api = <T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown) =>
  invoke<T>("account_api", { method, path, body: body ?? null });
const org = (orgId: string) => `/orgs/${encodeURIComponent(orgId)}`;

export const canManageTeam = (role: string) => role === "owner" || role === "admin";

export const createOrganization = (name: string) =>
  api<{ org: { id: string; name: string } }>("POST", "/orgs", {
    name,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });

export const listMembers = (orgId: string) => api<{ members: Member[] }>("GET", `${org(orgId)}/members`);

export const setMemberRole = (orgId: string, userId: string, role: Role) =>
  api<null>("PATCH", `${org(orgId)}/members/${encodeURIComponent(userId)}`, { role });

/** Remove someone, or leave (your own user id). */
export const removeMember = (orgId: string, userId: string) =>
  api<null>("DELETE", `${org(orgId)}/members/${encodeURIComponent(userId)}`);

/** A link anyone can use to join with `role`, for 14 days. */
export const createInvite = (orgId: string, role: Role) =>
  api<{ url: string }>("POST", `${org(orgId)}/invites`, { role });

export const acceptInvite = (invite: string) =>
  api<{ membership: { orgName: string; role: Role }; joined: boolean }>("POST", "/invites/accept", { invite: invite.trim() });

// --- The live set -------------------------------------------------------------------

/** Roles that run the live set (publish a service to the organization's room). */
export const canRunLive = (role: string) => role === "owner" || role === "admin" || role === "operator";
/** Roles that make band links. */
export const canManageBandLinks = canRunLive;

/**
 * Which organization this computer's services go to: the one whose library
 * is open (`openLibraryId`), so a service goes live where its songs are.
 * With this computer's own library open, the first organization whose role
 * runs the live set. Null when signed out or in none that can.
 */
export function liveOrganization(status: AccountStatus | null, openLibraryId?: string | null): Membership | null {
  if (!status?.signedIn) return null;
  const eligible = status.memberships.filter((m) => canRunLive(m.role));
  const open = status.memberships.find((m) => m.orgId === openLibraryId);
  if (open) return canRunLive(open.role) ? open : null;
  return eligible[0] ?? null;
}

/** A one-use address for this computer to connect to the live room as the operator. */
export const createLiveTicket = (orgId: string) => api<{ url: string }>("POST", `${org(orgId)}/live/ticket`);

export interface BandLink {
  device: { id: string; name: string; canLead: boolean };
  /** Open on a phone or tablet; shown once. */
  url: string;
}

/** A link for a band device (or, with `canLead`, the worship leader's). */
export const createBandLink = (orgId: string, name: string, canLead: boolean) =>
  api<BandLink>("POST", `${org(orgId)}/band-devices`, { name, canLead });
