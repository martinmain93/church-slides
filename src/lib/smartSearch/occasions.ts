/**
 * Occasions in the church year, and what songs for them tend to say.
 *
 * "Easter" is almost never a word in an Easter song, so an occasion is
 * searched as lyric-like phrases instead of its name. This table is the only
 * knowledge search needs beyond the songs themselves: without it, occasion
 * searches found 0.10 of the right songs in the top ten; with it, 0.60
 * (research/spikes/semantic_search).
 */

export interface Occasion {
  id: string;
  /** What the chip says. */
  label: string;
  /** Words in a query that name it (whole words, case-insensitive). */
  pattern: RegExp;
  /** Searched in place of the name: what its songs sing about. */
  phrases: string[];
}

export const OCCASIONS: Occasion[] = [
  {
    id: "good-friday",
    label: "Good Friday",
    pattern: /\bgood friday\b/i,
    phrases: [
      "Jesus died on the cross for me",
      "nailed to the cross, the Lamb was slain",
      "His blood was shed at Calvary",
      "the wondrous cross where my Saviour died",
    ],
  },
  {
    id: "palm-sunday",
    label: "Palm Sunday",
    pattern: /\bpalm sunday\b/i,
    phrases: ["Hosanna to the King who comes", "blessed is He who comes in the name of the Lord"],
  },
  {
    id: "easter",
    label: "Easter",
    pattern: /\b(easter|resurrection sunday)\b/i,
    phrases: [
      "He is risen from the grave",
      "the empty tomb, death could not hold Him",
      "Jesus is alive, death is defeated",
      "on the third day He rose again",
    ],
  },
  {
    id: "christmas",
    label: "Christmas",
    pattern: /\b(christmas|xmas|nativity|carols?)\b/i,
    phrases: [
      "born in a manger in Bethlehem",
      "angels sing glory to the newborn King",
      "Christ the Saviour is born",
      "shepherds and wise men came to see the baby",
    ],
  },
  {
    id: "advent",
    label: "Advent",
    pattern: /\badvent\b/i,
    phrases: [
      "O come Emmanuel, ransom captive Israel",
      "we wait for the coming of the Lord",
      "prepare the way, the King is coming",
      "come, Lord Jesus, come",
    ],
  },
  {
    id: "communion",
    label: "Communion",
    pattern: /\b(communion|lord'?s supper|eucharist|lord'?s table)\b/i,
    phrases: [
      "His body broken, His blood poured out",
      "we remember the bread and the cup",
      "a table prepared for me",
      "washed in the blood of the Lamb",
    ],
  },
  {
    id: "pentecost",
    label: "Pentecost",
    pattern: /\bpentecost\b/i,
    phrases: [
      "Holy Spirit come, fall on us",
      "pour out Your Spirit, set a fire",
      "wind and fire of the Spirit",
      "Spirit of God, fill this place",
    ],
  },
  {
    id: "baptism",
    label: "Baptism",
    pattern: /\b(baptisms?|baptismal|baptizing|baptising)\b/i,
    phrases: [
      "buried with Christ and raised to new life",
      "I have decided to follow Jesus",
      "washed clean, a new creation",
    ],
  },
  {
    id: "thanksgiving",
    label: "Thanksgiving",
    pattern: /\b(thanksgiving|harvest)\b/i,
    phrases: ["we give thanks for every good gift", "thank You Lord for all You've done", "the harvest of the fields"],
  },
  {
    id: "remembrance",
    label: "Remembrance",
    pattern: /\b(remembrance|memorial|funerals?)\b/i,
    phrases: [
      "abide with me, fast falls the eventide",
      "it is well with my soul",
      "when I see You face to face in heaven",
    ],
  },
];
