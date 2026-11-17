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
/** Opens the system browser and waits (up to 5 minutes) for the reply. */
export const signIn = () => invoke<AccountStatus>("account_sign_in");
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
