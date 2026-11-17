/**
 * Is this song a hymn? Decided from the song, never asked of anyone.
 *
 * Three signals, any one is enough:
 *   - its title is a well-known hymn (the list below — titles are facts, and
 *     most of these texts are long in the public domain)
 *   - its copyright line says public domain (the Open Hymnal and the seed
 *     hymns write one)
 *   - it reads like one: several "thee / thou / thy / hath ..." words
 *
 * On a real 326-song library the wording signal alone found 18 of 34 hymns:
 * modern-English hymns and imported songs whose verses aren't tagged
 * reliably slip past it, which is what the title list is for.
 */

/** Lowercase, no brackets or punctuation: "It Is Well With My Soul (2)" ->
 *  "it is well with my soul". */
export function normalizeTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/\([^)]*\)|\[[^\]]*\]/g, " ")
    .replace(/[’']/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

const HYMN_TITLES = [
  "a mighty fortress is our god", "abide with me", "all creatures of our god and king",
  "all glory laud and honor", "all hail the power of jesus name", "all people that on earth do dwell",
  "all things bright and beautiful", "alas and did my saviour bleed", "alas and did my savior bleed",
  "amazing grace", "and can it be", "angels from the realms of glory", "angels we have heard on high",
  "at calvary", "away in a manger", "be still my soul", "be thou my vision",
  "beautiful saviour", "before the throne of god above", "beneath the cross of jesus", "blessed assurance",
  "blest be the tie that binds", "breathe on me breath of god", "brethren we have met to worship",
  "christ the lord is risen today", "come thou almighty king", "come thou fount",
  "come thou fount of every blessing", "come thou long expected jesus", "come ye faithful raise the strain",
  "come ye thankful people come", "count your blessings", "crown him with many crowns",   "dear lord and father of mankind", "doxology", "fairest lord jesus", "faith of our fathers",
  "for all the saints", "for the beauty of the earth", "god be with you till we meet again",
  "god moves in a mysterious way", "god of grace and god of glory", "god rest ye merry gentlemen",
  "good christian men rejoice", "great is thy faithfulness", "guide me o thou great jehovah",
  "hallelujah what a saviour", "hallelujah what a savior", "hark the herald angels sing", "he leadeth me",
  "here is love", "his eye is on the sparrow", "holy holy holy", "how firm a foundation",
  "how great thou art", "how deep the fathers love for us", "i am thine o lord", "i have decided to follow jesus",
  "i know that my redeemer liveth", "i love to tell the story", "i need thee every hour", "i surrender all",
  "i will sing of my redeemer", "i will sing the wondrous story", "immortal invisible", "immortal invisible god only wise",
  "in christ alone", "in the bleak midwinter", "in the garden", "it came upon the midnight clear",
  "it is well with my soul", "jesus christ is risen today", "jesus loves me", "jesus lover of my soul",
  "jesus paid it all", "jesus shall reign", "jesus what a friend for sinners", "joy to the world",
  "joyful joyful we adore thee", "just as i am", "lead kindly light", "lead on o king eternal",
  "leaning on the everlasting arms", "let all mortal flesh keep silence", "lift high the cross",
  "lo how a rose eer blooming", "love divine all loves excelling",
  "low in the grave he lay", "make me a channel of your peace", "man of sorrows", "mine eyes have seen the glory",
  "morning has broken", "my faith looks up to thee", "my hope is built on nothing less", "my jesus i love thee",
  "my tribute", "nearer my god to thee", "nothing but the blood", "now thank we all our god",
  "o come all ye faithful", "o come o come emmanuel", "o for a thousand tongues to sing", "o god our help in ages past",
  "o holy night", "o little town of bethlehem", "o love that wilt not let me go", "o sacred head now wounded",
  "o the deep deep love of jesus", "o worship the king", "once in royal davids city", "onward christian soldiers",
  "praise my soul the king of heaven", "praise to the lord the almighty", "praise god from whom all blessings flow",
  "rock of ages", "savior like a shepherd lead us", "saviour like a shepherd lead us", "silent night",
  "softly and tenderly", "stand up stand up for jesus", "standing on the promises", "sweet hour of prayer",
  "take my life and let it be", "tell me the old old story", "the church s one foundation",
  "the churchs one foundation", "the day thou gavest", "the first noel", "the king of love my shepherd is",
  "the lord s my shepherd", "the lords my shepherd", "the old rugged cross", "the power of the cross",
  "the solid rock", "there is a fountain", "there is a green hill far away", "there is power in the blood",
  "this is my fathers world", "thine be the glory", "to god be the glory", "trust and obey",
  "turn your eyes upon jesus", "we gather together", "we plough the fields and scatter", "we three kings",
  "were you there", "what a friend we have in jesus", "what child is this", "when i survey the wondrous cross",
  "when morning gilds the skies", "when peace like a river", "while shepherds watched their flocks",
  "wonderful grace of jesus", "wonderful words of life", "ye servants of god", "you are my vision",
];

// Left out on purpose: short titles a well-known modern song shares, which
// the prefix match below would catch ("At The Cross (Love Ran Red)", Bethel's
// "It Is Well", "He Lives", "Day By Day").
const KNOWN = new Set(HYMN_TITLES);

const ARCHAIC = /\b(thee|thou|thy|thine|hath|doth|dost|art thou|o'er|'tis|ye|e'er|ne'er|wilt|shalt|canst|hast)\b/gi;

export interface HymnLike {
  title: string;
  body_text: string;
  copyright?: string | null;
}

export function isHymn(song: HymnLike): boolean {
  const title = normalizeTitle(song.title);
  if (KNOWN.has(title)) return true;
  // "Amazing Grace My Chains Are Gone", "Holy Holy Holy Nicaea": a known
  // title followed by more words is the same hymn under a longer name.
  for (const t of KNOWN) if (title.startsWith(t + " ")) return true;
  if (/public domain/i.test(song.copyright ?? "")) return true;
  const words = song.body_text.match(/[\p{L}']+/gu)?.length ?? 0;
  const archaic = song.body_text.match(ARCHAIC)?.length ?? 0;
  return archaic >= 3 && archaic / Math.max(words, 1) >= 0.01;
}
