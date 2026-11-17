/**
 * The name a new setlist gets: the service it's most likely for — today if
 * it's Sunday, otherwise the coming Sunday — e.g. "Sunday 5 Oct". A list of
 * setlists all called "New setlist" can't be told apart; dates can, and the
 * name is still one click to change.
 */
export function defaultSetlistName(now: Date = new Date()): string {
  const sunday = new Date(now);
  sunday.setDate(now.getDate() + ((7 - now.getDay()) % 7));
  const day = sunday.getDate();
  const month = sunday.toLocaleDateString("en-GB", { month: "short" });
  return `Sunday ${day} ${month}`;
}
