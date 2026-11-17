import { describe, expect, it } from "vitest";

import {
  chordToNashville,
  isChordLine,
  keyPrefersFlats,
  parseChord,
  parseNashvilleChord,
  splitWordIntoSyllables,
  transposeChord,
  transposeKey,
} from "@/lib/chords/chord";
import { alignSlides, renderChordRows, type ChordChart } from "@/lib/chords/chart";
import { fromChordText, toChordText } from "@/lib/chords/editorText";
import { decodeBytes, importChartFile } from "@/lib/chords/import";
import { parseHeader } from "@/lib/chords/import/text";
import { chartToSong } from "@/lib/chords/import/toSong";
import { resolveSlideSequence } from "@/lib/slideSequence";

describe("chord symbols", () => {
  it("parses roots, suffixes and slash basses", () => {
    expect(parseChord("F#m7")).toMatchObject({ root: 6, suffix: "m7", bass: null });
    expect(parseChord("G/B")).toMatchObject({ root: 7, bass: 11 });
    expect(parseChord("Bbmaj7")).toMatchObject({ root: 10, suffix: "maj7" });
    expect(parseChord("Csus4")).not.toBeNull();
    expect(parseChord("D(add9)")).not.toBeNull();
  });

  it("rejects lyric words", () => {
    for (const w of ["Go", "Be", "Am I", "Every", "God", "And", "Glory", "Des"]) {
      expect(parseChord(w)).toBeNull();
    }
  });

  it("detects chord lines but not lyric lines", () => {
    expect(isChordLine("G        C/E     D")).toBe(true);
    expect(isChordLine("| G . . . | C . . . |")).toBe(true);
    expect(isChordLine("Em  C  G  D  (x2)")).toBe(true);
    expect(isChordLine("Amazing grace how sweet the sound")).toBe(false);
    expect(isChordLine("A mighty fortress")).toBe(false);
    expect(isChordLine("N.C.")).toBe(true);
    expect(isChordLine("x2")).toBe(false);
  });

  it("accepts a minor slash bass and keeps it through transposition", () => {
    expect(isChordLine("G#m7/D#m   G#m7/C#   F#")).toBe(true);
    expect(transposeChord("G#m7/D#m", -2, "E")).toBe("F#m7/C#m");
  });

  it("transposes with key-appropriate spelling", () => {
    expect(transposeChord("G", 3, "Bb")).toBe("Bb");
    expect(transposeChord("D/F#", 3, "Bb")).toBe("F/A");
    expect(transposeChord("Em7", 2, "A")).toBe("F#m7");
    expect(transposeChord("C", 1, "Db")).toBe("Db");
    expect(transposeChord("(G)", 2, "A")).toBe("(A)");
    expect(transposeChord("N.C.", 5, "C")).toBe("N.C.");
    expect(transposeKey("G", 3)).toBe("Bb");
    expect(transposeKey("Em", 2)).toBe("F#m");
    expect(transposeKey("A", -2)).toBe("G");
    expect(keyPrefersFlats("F")).toBe(true);
    expect(keyPrefersFlats("Dm")).toBe(true);
    expect(keyPrefersFlats("E")).toBe(false);
  });
});

describe("section headers", () => {
  it("recognises the common spellings", () => {
    const labels = ["Verse 1", "VERSE 1:", "[Chorus]", "Chorus 2", "Pre-Chorus", "PRE CHORUS:", "Bridge x2", "(Tag)", "V1", "C:", "[V2]", "Interlude"];
    for (const l of labels) expect(parseHeader(l, false), l).not.toBeNull();
    expect(parseHeader("V1", false)!.label).toBe("Verse 1");
    expect(parseHeader("C:", false)!.label).toBe("Chorus");
  });

  it("doesn't mistake chords or lyrics for headers", () => {
    expect(parseHeader("C2", false)).toBeNull(); // Cadd9
    expect(parseHeader("B7", false)).toBeNull();
    expect(parseHeader("[C]", true)).toBeNull(); // bracketed chord in a ChordPro file
    expect(parseHeader("Chorus of angels singing", false)).toBeNull();
    expect(parseHeader("Bridge over the river", false)).toBeNull();
  });

  it("keeps chords written on the header line", () => {
    expect(parseHeader("Intro: G  C  D", false)).toMatchObject({ label: "Intro", rest: "G  C  D" });
  });
});

describe("ChordPro", () => {
  const cho = `{title: Amazing Grace}
{artist: John Newton}
{key: G}
{tempo: 72}

{start_of_verse: Verse 1}
A[G]mazing [G7]grace how [C]sweet the [G]sound
That [G]saved a wretch like [D]me
{end_of_verse}

{start_of_chorus}
[C]My chains are [G]gone
{end_of_chorus}

{sov: Verse 2}
'Twas [G]grace that taught my [C]heart to fear
{eov}

{chorus}
`;

  it("reads metadata, sections, inline chords, and {chorus} repeats", () => {
    const r = importChartFile("amazing.cho", cho);
    expect(r.error).toBeNull();
    const [s] = r.songs;
    expect(s!.format).toBe("chordpro");
    expect(s!.title).toBe("Amazing Grace");
    expect(s!.artist).toBe("John Newton");
    expect(s!.chart.key).toBe("G");
    expect(s!.chart.tempo).toBe(72);
    expect(s!.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Chorus", "Verse 2"]);
    const first = s!.chart.sections[0]!.stanzas[0]![0]!;
    expect(first.text).toBe("Amazing grace how sweet the sound");
    expect(first.chords[0]).toEqual({ pos: 1, chord: "G" });
    expect(s!.order).toEqual(["Verse 1", "Chorus", "Verse 2", "Chorus"]);

    const song = chartToSong(s!);
    expect(song.body_text).toContain("[Verse 1]\nAmazing grace how sweet the sound");
    expect(song.body_text).not.toContain("[G]");
    expect(song.slide_order).toBe("V1, C1, V2, C1");
  });

  it("splits {new_song} into several songs and skips tab blocks", () => {
    const r = importChartFile("set.cho", `{t: One}\n[G]Hello\n{sot}\ne|---3---|\n{eot}\n{new_song}\n{t: Two}\n[C]World`);
    expect(r.songs.map((s) => s.title)).toEqual(["One", "Two"]);
    expect(JSON.stringify(r.songs[0]!.chart)).not.toContain("---3---");
  });
});

describe("chords over lyrics", () => {
  const txt = `10,000 Reasons
Matt Redman

Key: G

Intro:
G   D   Em   C

Chorus
C        G         D          Em
Bless the Lord O my soul, O my soul
C          G          D
Worship His holy name

Verse 1
C         G         D            Em
The light breaks in on the hills of morning

Chorus

CCLI Song # 6016351
© 2011 Thankyou Music
For use solely with the SongSelect® Terms of Use. All rights reserved. www.ccli.com
CCLI License # 12345
`;

  it("aligns chord columns over lyrics and reads title, key, footer", () => {
    const r = importChartFile("reasons.txt", txt);
    const [s] = r.songs;
    expect(s!.title).toBe("10,000 Reasons");
    expect(s!.artist).toBe("Matt Redman");
    expect(s!.chart.key).toBe("G");
    expect(s!.ccli).toBe("6016351");
    expect(s!.copyright).toBe("2011 Thankyou Music");
    expect(s!.format).toBe("songselect-text");
    const chorus = s!.chart.sections.find((x) => x.label === "Chorus")!;
    const line = chorus.stanzas[0]![0]!;
    expect(line.text).toBe("Bless the Lord O my soul, O my soul");
    expect(line.chords.map((c) => c.chord)).toEqual(["C", "G", "D", "Em"]);
    expect(line.chords[1]!.pos).toBe(9); // over "Lord"
    // Intro chords are kept for the band but not on a slide.
    const intro = s!.chart.sections.find((x) => x.label === "Intro")!;
    expect(intro.stanzas[0]![0]!.text).toBe("");
    const song = chartToSong(s!);
    expect(song.body_text).not.toContain("Intro");
    expect(song.body_text).not.toMatch(/CCLI|SongSelect/);
    expect(song.slide_order).toBe("C1, V, C1");
  });

  it("reads Ultimate Guitar markup", () => {
    const ug = `[Verse 1]\n[ch]G[/ch]              [ch]C[/ch]\nHere I am to worship`;
    const [s] = importChartFile("ug.txt", ug).songs;
    expect(s!.format).toBe("ultimate-guitar");
    expect(s!.chart.sections[0]!.stanzas[0]![0]!.chords.map((c) => c.pos)).toEqual([0, 15]);
  });

  it("reads OnSong-style metadata with inline chords", () => {
    const onsong = `Way Maker\nSinach\nKey: E\n\nVerse 1:\nYou are [E]here, moving in our [B]midst\n\nChorus:\nWay [E]maker, miracle [B]worker`;
    const [s] = importChartFile("way.onsong", onsong).songs;
    expect(s!.format).toBe("onsong");
    expect(s!.title).toBe("Way Maker");
    expect(s!.chart.key).toBe("E");
    expect(s!.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Chorus"]);
  });

  it("imports plain lyrics without inventing a title from the first line", () => {
    const [s] = importChartFile("my song.txt", "Line one of the song\nLine two of the song\nLine three").songs;
    expect(s!.title).toBe("My Song");
    expect(chartToSong(s!).body_text).toContain("Line one of the song");
  });

  it("reads the app's own format as a lyrics-only import", () => {
    const [s] = importChartFile("x.txt", "[Verse 1]\nHello there\n\n[Chorus]\nSing it").songs;
    expect(s!.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Chorus"]);
  });
});

describe("XML formats", () => {
  it("reads OpenLyrics with chords and verse order", () => {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties>
    <titles><title>Amazing Grace</title></titles>
    <authors><author>John Newton</author></authors>
    <key>G</key>
    <verseOrder>v1 c v2 c</verseOrder>
  </properties>
  <lyrics>
    <verse name="v1"><lines><chord name="G"/>Amazing grace how <chord name="C"/>sweet<br/>That saved a wretch</lines></verse>
    <verse name="c"><lines>My chains are gone</lines></verse>
    <verse name="v2"><lines>'Twas grace</lines></verse>
  </lyrics>
</song>`;
    const [s] = importChartFile("ag.xml", xml).songs;
    expect(s!.format).toBe("openlyrics");
    expect(s!.chart.key).toBe("G");
    expect(s!.order).toEqual(["Verse 1", "Chorus", "Verse 2", "Chorus"]);
    const l = s!.chart.sections[0]!.stanzas[0]!;
    expect(l[0]).toEqual({ text: "Amazing grace how sweet", chords: [{ pos: 0, chord: "G" }, { pos: 18, chord: "C" }] });
    expect(l[1]!.text).toBe("That saved a wretch");
    expect(chartToSong(s!).slide_order).toBe("V1, C1, V2, C1");
  });

  it("reads OpenSong chord lines and stacked verses", () => {
    const xml = `<song><title>Test</title><key>D</key><presentation>V1 C V2</presentation><lyrics>[V]
.D        G
1Amazing grace
2Twas grace that
[C]
.A
 My chains
</lyrics></song>`;
    const [s] = importChartFile("t.xml", xml).songs;
    expect(s!.format).toBe("opensong");
    expect(s!.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Verse 2", "Chorus"]);
    expect(s!.chart.sections[1]!.stanzas[0]![0]!.chords.map((c) => c.chord)).toEqual(["D", "G"]);
    expect(s!.order).toEqual(["Verse 1", "Chorus", "Verse 2"]);
  });

  it("reads SongSelect .usr", () => {
    const usr = `[S A22025]\nTitle=Amazing Grace\nAuthor=John Newton\nKeys=G\nFields=Verse 1/tChorus\nWords=Amazing grace/nHow sweet/n/nThat saved/tMy chains`;
    const [s] = importChartFile("ag.usr", usr).songs;
    expect(s!.format).toBe("songselect-usr");
    expect(s!.ccli).toBe("22025");
    expect(s!.chart.sections[0]!.stanzas).toHaveLength(2);
  });

  it("explains unsupported files", () => {
    expect(importChartFile("x.docx", "PK\u0003\u0004...").error).toMatch(/Office/);
  });
});

describe("decoding", () => {
  it("handles UTF-16 with a BOM and legacy Windows-1252", () => {
    const utf16 = new Uint8Array([0xff, 0xfe, 0x41, 0x00, 0x42, 0x00]);
    expect(decodeBytes(utf16)).toBe("AB");
    expect(decodeBytes(new Uint8Array([0x93, 0x68, 0x69, 0x94]))).toBe("“hi”");
  });
});

describe("aligning a chart to slides", () => {
  const chart: ChordChart = {
    version: 1, key: "G", tempo: null, time: null,
    sections: [
      { label: "Intro", stanzas: [[{ text: "", chords: [{ pos: 0, chord: "G" }, { pos: 4, chord: "D" }] }]] },
      {
        label: "Verse 1",
        stanzas: [[
          { text: "Amazing grace how sweet the sound", chords: [{ pos: 1, chord: "G" }, { pos: 18, chord: "C" }] },
          { text: "That saved a wretch like me", chords: [{ pos: 23, chord: "D" }] },
        ]],
      },
      { label: "Turnaround", stanzas: [[{ text: "", chords: [{ pos: 0, chord: "Em" }] }]] },
      { label: "Chorus", stanzas: [[{ text: "My chains are gone", chords: [{ pos: 0, chord: "C" }] }]] },
    ],
  };

  it("finds chords even when the slide breaks lines differently", () => {
    const slides = [
      { lyrics: "" }, // leading blank
      { lyrics: "Amazing grace\nhow sweet the sound that saved a wretch like me" },
      { lyrics: "My chains are gone" },
      { lyrics: "Some lyric not in the chart at all" },
    ];
    const out = alignSlides(chart, slides);
    expect(out[0]!.after[0]!.label).toBe("Intro");
    expect(out[1]!.matched).toBe(true);
    expect(out[1]!.lines[0]!.chords).toEqual([{ pos: 1, chord: "G" }]);
    // "sweet" is the 1st word of line 2 here, so C lands at its start.
    expect(out[1]!.lines[1]!.chords[0]).toEqual({ pos: 4, chord: "C" });
    expect(out[1]!.after.map((a) => a.label)).toEqual(["Turnaround"]);
    expect(out[2]!.lines[0]!.chords[0]!.chord).toBe("C");
    expect(out[3]!.matched).toBe(false);
  });

  it("aligns against slides resolved from the imported song", () => {
    const [s] = importChartFile("x.cho", "{title: X}\n{sov: Verse 1}\n[G]Hello [C]world\n\n[D]Second [G]slide\n{eov}\n{soc}\n[Em]Chorus [C]line\n{eoc}\n{chorus}").songs;
    const song = chartToSong(s!);
    const slides = resolveSlideSequence(song.body_text, song.slide_order, null, { leadingBlank: true });
    const aligned = alignSlides(s!.chart, slides);
    expect(aligned.map((a) => a.lines.flatMap((l) => l.chords.map((c) => c.chord)))).toEqual([
      [], ["G", "C"], ["D", "G"], ["Em", "C"], ["Em", "C"],
    ]);
  });

  it("renders chords over lyrics without collisions", () => {
    const rows = renderChordRows({ text: "Amazing", chords: [{ pos: 0, chord: "Gmaj7" }, { pos: 2, chord: "C" }] });
    expect(rows.chords).toBe("Gmaj7 C");
    expect(rows.lyrics.replace(/ /g, "")).toBe("Amazing");
  });
});

describe("chords between words", () => {
  it("attach to the following word", () => {
    const chart: ChordChart = {
      version: 1, key: "C", tempo: null, time: null,
      sections: [{ label: "V", stanzas: [[{ text: "Softly I heard a call", chords: [{ pos: 16, chord: "G" }] }]] }],
    };
    const [a] = alignSlides(chart, [{ lyrics: "Softly I heard\na call" }]);
    expect(a!.lines[1]!.chords).toEqual([{ pos: 2, chord: "G" }]);
  });
});

import { fromFileName } from "@/lib/chords/import/fileName";

describe("titles and keys from file names", () => {
  it.each([
    ["goodness-of-god-G.pdf", "Goodness Of God", "G"],
    ["Holy Forever-chords-Bb.pdf", "Holy Forever", "Bb"],
    ["Build My Life (37195) - Chord Chart - C - 2 Column.pdf", "Build My Life", "C"],
    ["Cornerstone (22647) - Chord Chart -  C - 2 Column-2.pdf", "Cornerstone", "C"],
    ["How-Great-is-Our-God-Key-A.pdf", "How Great is Our God", "A"],
    ["Nothing-I-Hold-Onto-Key-of-G-Will-Reagan.pdf", "Nothing I Hold Onto Will Reagan", "G"],
    ["Amazing Grace (My Chains Are Gone)  (Chris Tomlin) Chord Chart in Db.pdf", "Amazing Grace (My Chains Are Gone) (Chris Tomlin)", "Db"],
    ["Do It Again Chords by Elevation Worshiptabs @ Ultimate Guitar Archive.pdf", "Do It Again", null],
    ["Majesty (Here I Am) (A).pdf", "Majesty (Here I Am)", "A"],
    ["Tremble -C.pdf", "Tremble", "C"],
    ["14418-dry-bones.pdf", "Dry Bones", null],
    ["Hosanna (Praise Is Rising)-G .pdf", "Hosanna (Praise Is Rising)", "G"],
    ["joy_to_the_world.pdf", "Joy To The World", null],
    ["10,000 Reasons (Bless The Lord)-G _2.pdf", "10,000 Reasons (Bless The Lord)", "G"],
  ])("%s", (name, title, key) => {
    expect(fromFileName(name)).toEqual({ title, key });
  });
});

describe("Nashville Number System & syllable segmentation", () => {
  it("parses Nashville numbers (1-7, 6m, 2m7, 1/3, 5/7, b7) into concrete chords for any key", () => {
    expect(parseNashvilleChord("1", "G")).toBe("G");
    expect(parseNashvilleChord("2", "G")).toBe("Am");
    expect(parseNashvilleChord("3", "G")).toBe("Bm");
    expect(parseNashvilleChord("4", "G")).toBe("C");
    expect(parseNashvilleChord("5", "G")).toBe("D");
    expect(parseNashvilleChord("6", "G")).toBe("Em");
    expect(parseNashvilleChord("6m", "G")).toBe("Em");
    expect(parseNashvilleChord("6M", "G")).toBe("E");
    expect(parseNashvilleChord("6m7", "G")).toBe("Em7");
    expect(parseNashvilleChord("1/3", "G")).toBe("G/B");
    expect(parseNashvilleChord("5/7", "G")).toBe("D/F#");
    expect(parseNashvilleChord("b7", "G")).toBe("F");
    expect(parseNashvilleChord("4sus2", "Bb")).toBe("Ebsus2");
    expect(parseNashvilleChord("1/3", "Bb")).toBe("Bb/D");
  });

  it("converts chords back into Nashville numbers relative to the song's key", () => {
    expect(chordToNashville("G", "G")).toBe("1");
    expect(chordToNashville("Am7", "G")).toBe("2m7");
    expect(chordToNashville("G/B", "G")).toBe("1/3");
    expect(chordToNashville("C", "G")).toBe("4");
    expect(chordToNashville("D/F#", "G")).toBe("5/7");
    expect(chordToNashville("Em7", "G")).toBe("6m7");
    expect(chordToNashville("F", "G")).toBe("b7");
  });

  it("splits multi-syllable words into natural singing syllables while keeping silent -e/-ed attached", () => {
    expect(splitWordIntoSyllables("Amazing").map((s) => s.text)).toEqual(["A", "ma", "zing"]);
    expect(splitWordIntoSyllables("Hallelujah").map((s) => s.text)).toEqual(["Hal", "le", "lu", "jah"]);
    expect(splitWordIntoSyllables("surrender").map((s) => s.text)).toEqual(["sur", "ren", "der"]);
    expect(splitWordIntoSyllables("grace").map((s) => s.text)).toEqual(["grace"]);
    expect(splitWordIntoSyllables("saved").map((s) => s.text)).toEqual(["saved"]);
  });
});

describe("multi-format importer -> editor -> presentation round-trip", () => {
  const formatSamples: { name: string; filename: string; content: string; expectedChords: string[] }[] = [
    {
      name: "ChordPro with mid-word syllables, trailing chords, and turnaround",
      filename: "worship.cho",
      content: [
        "{title: Hallelujah Song}",
        "{key: G}",
        "{sov: Intro}",
        "[G] [D] [Em] [C]",
        "{eov}",
        "{sov: Verse 1}",
        "Hal[G]le[D]lu[Em]jah for[C]ever [D]",
        "{eov}",
        "{sov: Turnaround}",
        "[Em] [C] [G] [D]",
        "{eov}",
        "{soc}",
        "[C]Sing a[G]loud",
        "{eoc}",
      ].join("\n"),
      expectedChords: ["G", "D", "Em", "C", "D"],
    },
    {
      name: "OnSong with inline chords and trailing chord",
      filename: "onsong.onsong",
      content: [
        "Cornerstone",
        "Hillsong",
        "Key: C",
        "",
        "Intro:",
        "C  Am  F  G",
        "",
        "Verse 1:",
        "My [C]hope is built on [Am]nothing less [G]",
        "",
        "Chorus:",
        "[F]Christ a[Am]lone [G]corner[C]stone",
      ].join("\n"),
      expectedChords: ["C", "Am", "G"],
    },
    {
      name: "SongSelect two-line text with intro, turnaround, and trailing chords",
      filename: "songselect.txt",
      content: [
        "Goodness of God",
        "Bethel Music",
        "Key: A",
        "",
        "Intro",
        "A   D   A   E",
        "",
        "Verse 1",
        "A                  D              A    E",
        "I will rise and sing of mercy in the morning",
        "",
        "Turnaround",
        "F#m   D   A   E",
        "",
        "Chorus",
        "D                  A",
        "All my days You have been near me",
      ].join("\n"),
      expectedChords: ["A", "D", "A", "E"],
    },
    {
      name: "Ultimate Guitar [ch] markup",
      filename: "ug.txt",
      content: [
        "[Verse 1]",
        "[ch]G[/ch]              [ch]C[/ch]         [ch]D[/ch]",
        "Here I am to worship",
        "",
        "[Chorus]",
        "[ch]G[/ch]                  [ch]D/F#[/ch]",
        "You're altogether lovely",
      ].join("\n"),
      expectedChords: ["G", "C", "D"],
    },
    {
      name: "OpenLyrics XML",
      filename: "openlyrics.xml",
      content: `<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>OpenLyrics Test</title></titles><key>D</key></properties>
  <lyrics>
    <verse name="v1"><lines><chord name="D"/>Holy <chord name="G"/>holy <chord name="A"/>Lord</lines></verse>
    <verse name="c1"><lines><chord name="Bm"/>Worthy <chord name="G"/>is the <chord name="D"/>Lamb</lines></verse>
  </lyrics>
</song>`,
      expectedChords: ["D", "G", "A"],
    },
    {
      name: "OpenSong XML",
      filename: "opensong.xml",
      content: `<song><title>OpenSong Test</title><key>E</key><lyrics>[V1]
.E        A        B
1Amazing love how can it be
[C]
.A        E        B
 You are my King
</lyrics></song>`,
      expectedChords: ["E", "A", "B"],
    },
  ];

  it.each(formatSamples)(
    "imports, round-trips through editor text, and aligns cleanly for presentation: $name",
    ({ filename, content, expectedChords }) => {
      const res = importChartFile(filename, content);
      expect(res.error).toBeNull();
      const imported = res.songs[0]!;
      const songInput = chartToSong(imported);

      // Projector body_text must never leak inline bracket chords
      expect(songInput.body_text).not.toMatch(/\[[A-G][#b]?m?\d?\]/);

      // Round-trip through editor text (toChordText -> fromChordText) preserves body and chords
      const editorText = toChordText(songInput.body_text, imported.chart);
      const roundTripped = fromChordText(editorText, imported.chart);
      expect(roundTripped.body).toBe(songInput.body_text);
      expect(roundTripped.chart).not.toBeNull();

      // Presentation slide sequence + alignSlides matches every lyric slide
      const presentationSlides = resolveSlideSequence(
        roundTripped.body,
        songInput.slide_order,
        null,
        { leadingBlank: true },
      );
      const aligned = alignSlides(roundTripped.chart!, presentationSlides);
      // Slide 1 is the first lyric slide (Verse 1)
      expect(aligned[1]!.matched).toBe(true);
      expect(aligned[1]!.lines[0]!.chords.map((c) => c.chord)).toEqual(expectedChords);
    },
  );
});

