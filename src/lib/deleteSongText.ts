/**
 * What both apps say before deleting a song: which services it's in (they
 * lose it), so nobody finds a hole in Sunday's service by surprise. One
 * rule everywhere: say so, then delete it and take it out of them.
 */
export function deleteSongMessage(serviceNames: string[]): string {
  if (serviceNames.length === 0) return "It goes from the library, and can't be undone.";
  const named = serviceNames.slice(0, 3).map((n) => n || "Untitled service");
  const more = serviceNames.length > 3 ? `, and ${serviceNames.length - 3} more` : "";
  const list = `${named.join(", ")}${more}`;
  const where = serviceNames.length === 1 ? `It's in 1 service (${list}).` : `It's in ${serviceNames.length} services (${list}).`;
  return `${where} It comes out of them, and out of the library. This can't be undone.`;
}
