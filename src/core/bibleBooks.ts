/**
 * Bible books, for tidying and checking a reference someone types ("ps 23"
 * -> "Psalm 23", "Hezekiah 4:2" -> "No book called Hezekiah") before it's
 * saved into a service, so a mistake shows while planning, not on Sunday
 * morning when the slides computer can't find the verses.
 *
 * The 66 books, and the deuterocanonical books Catholic and Anglican
 * churches read, with their usual short forms and chapter counts.
 */

interface Book {
  name: string;
  chapters: number;
  /** Other ways it's written, besides any start of its name. */
  aliases?: string[];
}

const BOOKS: Book[] = [
  { name: "Genesis", chapters: 50, aliases: ["gn"] },
  { name: "Exodus", chapters: 40, aliases: ["ex"] },
  { name: "Leviticus", chapters: 27, aliases: ["lv"] },
  { name: "Numbers", chapters: 36, aliases: ["nm", "nb"] },
  { name: "Deuteronomy", chapters: 34, aliases: ["dt"] },
  { name: "Joshua", chapters: 24, aliases: ["jos", "jsh"] },
  { name: "Judges", chapters: 21, aliases: ["jdg", "jg"] },
  { name: "Ruth", chapters: 4, aliases: ["rt"] },
  { name: "1 Samuel", chapters: 31, aliases: ["1 sm"] },
  { name: "2 Samuel", chapters: 24, aliases: ["2 sm"] },
  { name: "1 Kings", chapters: 22, aliases: ["1 kgs"] },
  { name: "2 Kings", chapters: 25, aliases: ["2 kgs"] },
  { name: "1 Chronicles", chapters: 29, aliases: ["1 chr"] },
  { name: "2 Chronicles", chapters: 36, aliases: ["2 chr"] },
  { name: "Ezra", chapters: 10 },
  { name: "Nehemiah", chapters: 13 },
  { name: "Esther", chapters: 10 },
  { name: "Job", chapters: 42 },
  { name: "Psalm", chapters: 150, aliases: ["psalms", "pss", "psa", "ps"] },
  { name: "Proverbs", chapters: 31, aliases: ["prv"] },
  { name: "Ecclesiastes", chapters: 12, aliases: ["qoh"] },
  { name: "Song of Songs", chapters: 8, aliases: ["song of solomon", "song", "sos", "canticles"] },
  { name: "Isaiah", chapters: 66 },
  { name: "Jeremiah", chapters: 52 },
  { name: "Lamentations", chapters: 5 },
  { name: "Ezekiel", chapters: 48, aliases: ["ezk"] },
  { name: "Daniel", chapters: 12, aliases: ["dn"] },
  { name: "Hosea", chapters: 14 },
  { name: "Joel", chapters: 3 },
  { name: "Amos", chapters: 9 },
  { name: "Obadiah", chapters: 1 },
  { name: "Jonah", chapters: 4, aliases: ["jon"] },
  { name: "Micah", chapters: 7 },
  { name: "Nahum", chapters: 3 },
  { name: "Habakkuk", chapters: 3 },
  { name: "Zephaniah", chapters: 3 },
  { name: "Haggai", chapters: 2 },
  { name: "Zechariah", chapters: 14 },
  { name: "Malachi", chapters: 4 },
  { name: "Tobit", chapters: 14 },
  { name: "Judith", chapters: 16, aliases: ["jdt"] },
  { name: "Wisdom", chapters: 19, aliases: ["wisdom of solomon", "wis"] },
  { name: "Sirach", chapters: 51, aliases: ["ecclesiasticus", "sir"] },
  { name: "Baruch", chapters: 6 },
  { name: "1 Maccabees", chapters: 16, aliases: ["1 macc"] },
  { name: "2 Maccabees", chapters: 15, aliases: ["2 macc"] },
  { name: "Matthew", chapters: 28, aliases: ["mt"] },
  { name: "Mark", chapters: 16, aliases: ["mk", "mrk"] },
  { name: "Luke", chapters: 24, aliases: ["lk"] },
  { name: "John", chapters: 21, aliases: ["jn", "jhn"] },
  { name: "Acts", chapters: 28 },
  { name: "Romans", chapters: 16, aliases: ["rm"] },
  { name: "1 Corinthians", chapters: 16 },
  { name: "2 Corinthians", chapters: 13 },
  { name: "Galatians", chapters: 6 },
  { name: "Ephesians", chapters: 6 },
  { name: "Philippians", chapters: 4, aliases: ["php", "phil"] },
  { name: "Colossians", chapters: 4 },
  { name: "1 Thessalonians", chapters: 5 },
  { name: "2 Thessalonians", chapters: 3 },
  { name: "1 Timothy", chapters: 6 },
  { name: "2 Timothy", chapters: 4 },
  { name: "Titus", chapters: 3 },
  { name: "Philemon", chapters: 1, aliases: ["phlm", "phm"] },
  { name: "Hebrews", chapters: 13 },
  { name: "James", chapters: 5, aliases: ["jas", "jm"] },
  { name: "1 Peter", chapters: 5, aliases: ["1 pt"] },
  { name: "2 Peter", chapters: 3, aliases: ["2 pt"] },
  { name: "1 John", chapters: 5, aliases: ["1 jn"] },
  { name: "2 John", chapters: 1, aliases: ["2 jn"] },
  { name: "3 John", chapters: 1, aliases: ["3 jn"] },
  { name: "Jude", chapters: 1 },
  { name: "Revelation", chapters: 22, aliases: ["revelations", "apocalypse", "rv"] },
];

/** Lower case, no dots, "I"/"II"/"First" as numbers, one space. */
function key(s: string): string {
  return s
    .toLowerCase()
    .replace(/\./g, "")
    .replace(/^(iii|3rd|third)\s*/, "3 ")
    .replace(/^(ii|2nd|second)\s*/, "2 ")
    .replace(/^(i|1st|first)\s+/, "1 ")
    .replace(/^([1-3])\s*/, "$1 ")
    .replace(/\s+/g, " ")
    .trim();
}

/** The book `typed` names: its full name or alias, else the one book whose
 * name starts with it ("gen", "1 cor"); null when none or several do. */
export function findBook(typed: string): Book | null {
  const k = key(typed);
  if (!k) return null;
  const exact = BOOKS.find((b) => key(b.name) === k || b.aliases?.some((a) => key(a) === k));
  if (exact) return exact;
  const starts = booksStarting(k);
  return starts.length === 1 ? starts[0]! : null;
}

function booksStarting(k: string): Book[] {
  return BOOKS.filter((b) => key(b.name).startsWith(k) && k.replace(/^\d /, "").length >= 2);
}

export type CheckedReference = { reference: string } | { problem: string };

/**
 * A typed reference, tidied ("ps 23" -> "Psalm 23", "jn 3:16-17" ->
 * "John 3:16–17"), or what's wrong with it in one line.
 */
export function checkReference(typed: string): CheckedReference {
  const t = typed.trim().replace(/\s+/g, " ");
  if (!t) return { problem: "Type a passage, like John 3:16." };
  const m = /^((?:[1-3]|i{1,3}|first|second|third|1st|2nd|3rd)?\s*[a-z][a-z .']*?)\s*(\d{1,3})(?:\s*[:.v]\s*(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?)?$/i.exec(t);
  if (!m) return { problem: "Write it like John 3:16 or Psalm 23." };
  const book = findBook(m[1]!);
  if (!book) {
    const several = booksStarting(key(m[1]!));
    if (several.length > 1) return { problem: `Which book: ${several.slice(0, 3).map((b) => b.name).join(" or ")}?` };
    return { problem: `No book called ${m[1]!.trim()}.` };
  }
  // A one-chapter book is quoted by verse alone: "Jude 24" is Jude 1:24.
  if (book.chapters === 1 && !m[3] && Number(m[2]) > 1) {
    return { reference: `${book.name} 1:${Number(m[2])}` };
  }
  const chapter = Number(m[2]);
  if (chapter < 1 || chapter > book.chapters) {
    return { problem: `${book.name} has ${book.chapters} chapter${book.chapters === 1 ? "" : "s"}.` };
  }
  const first = m[3] ? Number(m[3]) : null;
  const last = m[4] ? Number(m[4]) : null;
  if (first !== null && first < 1) return { problem: "Verses start at 1." };
  if (first !== null && last !== null && last < first) return { problem: `The last verse comes before verse ${first}.` };
  const verses = first === null ? "" : `:${first}${last !== null && last !== first ? `–${last}` : ""}`;
  return { reference: `${book.name} ${chapter}${verses}` };
}
