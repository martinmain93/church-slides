/** A role's name as people see it, the same on every client. */
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
