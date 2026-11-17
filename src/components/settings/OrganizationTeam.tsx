import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  ASSIGNABLE_ROLES,
  canManageTeam,
  createInvite,
  listMembers,
  removeMember,
  roleLabel,
  setMemberRole,
  type Member,
  type Membership,
  type Role,
} from "@/lib/account";
import { useConfirm } from "@/lib/confirm";
import { useToast } from "@/lib/toast";

const selectClass = "h-8 rounded-md border border-border bg-background px-2 text-sm";

/**
 * One organization's team, opened from Settings → Account: who's in it and
 * their roles. Owners and admins also invite people, change roles and
 * remove members; everyone else can leave.
 */
export function OrganizationTeam({
  membership,
  myUserId,
  onLeft,
}: {
  membership: Membership;
  myUserId: string | null;
  onLeft: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const manage = canManageTeam(membership.role);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [inviteRole, setInviteRole] = useState<Role>("member");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown) => toast.error(err instanceof Error ? err.message : String(err));

  useEffect(() => {
    let live = true;
    listMembers(membership.orgId).then((r) => live && setMembers(r.members), fail);
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the organization changes
  }, [membership.orgId]);

  async function step(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setMembers((await listMembers(membership.orgId)).members);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function invite() {
    setBusy(true);
    try {
      setInviteUrl((await createInvite(membership.orgId, inviteRole)).url);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied.");
    } catch {
      toast.error("Couldn't copy — select the link and copy it instead.");
    }
  }

  async function remove(m: Member) {
    const ok = await confirm({
      title: `Remove ${m.displayName ?? m.email ?? "this person"}?`,
      message: `They'll no longer see ${membership.orgName}'s library or live set.`,
      confirmLabel: "Remove",
      variant: "destructive",
    });
    if (ok) await step(() => removeMember(membership.orgId, m.userId));
  }

  async function leave() {
    if (!myUserId) return;
    const ok = await confirm({
      title: `Leave ${membership.orgName}?`,
      message: "You'll need a new invite to join again.",
      confirmLabel: "Leave",
      variant: "destructive",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await removeMember(membership.orgId, myUserId);
      onLeft();
    } catch (err) {
      fail(err);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      {members === null ? (
        <p className="text-xs text-muted-foreground">Loading the team…</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {members.map((m) => {
            const editable = manage && m.role !== "owner" && m.userId !== myUserId;
            return (
              <li key={m.userId} className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate">
                    {m.displayName ?? m.email}
                    {m.userId === myUserId && <span className="text-muted-foreground"> (you)</span>}
                  </p>
                  {manage && m.displayName && m.email && <p className="truncate text-xs text-muted-foreground">{m.email}</p>}
                </div>
                {editable ? (
                  <div className="flex shrink-0 items-center gap-1">
                    <select
                      aria-label={`Role for ${m.displayName ?? m.email}`}
                      value={m.role}
                      disabled={busy}
                      onChange={(e) => void step(() => setMemberRole(membership.orgId, m.userId, e.target.value as Role))}
                      className={selectClass}
                    >
                      {ASSIGNABLE_ROLES.map((r) => (
                        <option key={r} value={r}>
                          {roleLabel(r)}
                        </option>
                      ))}
                    </select>
                    <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void remove(m)}>
                      Remove
                    </Button>
                  </div>
                ) : (
                  <span className="shrink-0 text-muted-foreground">{roleLabel(m.role)}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {manage && (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center gap-2">
            <select
              aria-label="Role for new people"
              value={inviteRole}
              disabled={busy}
              onChange={(e) => {
                setInviteRole(e.target.value as Role);
                setInviteUrl(null);
              }}
              className={selectClass}
            >
              {ASSIGNABLE_ROLES.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void invite()}>
              Invite people
            </Button>
          </div>
          {inviteUrl && (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  aria-label="Invite link"
                  value={inviteUrl}
                  onFocus={(e) => e.currentTarget.select()}
                  className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-xs"
                />
                <Button type="button" size="sm" onClick={() => void copy(inviteUrl)}>
                  Copy
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Anyone with this link can join as {roleLabel(inviteRole).toLowerCase()} for the next 14 days.
              </p>
            </div>
          )}
        </div>
      )}

      {membership.role !== "owner" && (
        <div className="border-t border-border pt-3">
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void leave()}>
            Leave {membership.orgName}
          </Button>
        </div>
      )}
    </div>
  );
}
