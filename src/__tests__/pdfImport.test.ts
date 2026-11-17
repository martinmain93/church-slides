import { describe, expect, it } from "vitest";

import { pagesToChartText, splitColumns, type PdfTextItem } from "@/lib/chords/import/pdfLayout";
import { parseFactsLine, parseTextChart, roadmapLine, scanCcli, tidyLyricLine } from "@/lib/chords/import/text";

/** A run of text at (x, y) with a width from Helvetica-ish metrics. */
function run(str: string, x: number, y: number, size = 12): PdfTextItem {
  return { str, x, y, width: str.length * size * 0.5, size };
}

describe("PDF chord sheets", () => {
  it("puts each chord on the lyric character beneath it", () => {
    // One run per word, as many PDFs are built: chords sit at "Lord" and "mercy".
    const words = "We thank You Lord for all Your mercy shown".split(" ");
    let x = 40;
    const lyric = words.map((w) => {
      const r = run(w, x, 686);
      x += r.width + 4;
      return r;
    });
    const at = (w: string) => lyric.find((r) => r.str === w)!.x;
    const text = pagesToChartText([[
      run("Title", 40, 760, 15),
      run("G", at("Lord"), 700), run("C", at("mercy"), 700),
      ...lyric,
    ]]);
    expect(text).toContain("We thank You [G]Lord for all Your [C]mercy");
  });

  it("joins a chord's raised extension back onto it", () => {
    const text = pagesToChartText([[
      run("Title", 40, 760, 15),
      run("A long lyric line that sets the body text size nicely", 40, 600),
      run("Dmaj", 40, 700), { str: "7", x: 64, y: 704.8, width: 5, size: 10 },
      run("G", 120, 700), { str: "2", x: 126, y: 704.8, width: 5, size: 10 },
      // A stray space run at superscript height must not capture them.
      { str: " ", x: 20, y: 704.8, width: 3, size: 12 },
      run("I don't believe in fairytales", 40, 686),
    ]]);
    expect(text).toMatch(/\[Dmaj7\]I don't/);
    expect(text).toContain("[G2]");
  });

  it("reads a two-column page left column first", () => {
    const left = Array.from({ length: 12 }, (_, i) => run(`left line ${i}`, 40, 700 - i * 14));
    const right = Array.from({ length: 12 }, (_, i) => run(`right line ${i}`, 330, 700 - i * 14));
    const heading = run("A Title That Spans The Whole Page Width Here Yes", 40, 740, 15);
    const blocks = splitColumns([heading, ...left, ...right]);
    expect(blocks.length).toBe(3);
    const text = pagesToChartText([[heading, ...left, ...right]]);
    expect(text.indexOf("left line 11")).toBeLessThan(text.indexOf("right line 0"));
  });
});

describe("sheet headings and footers", () => {
  it("reads key, tempo and time however they're written", () => {
    for (const [line, key, tempo, time] of [
      ["Key - G | Tempo - 63 | Time - 4/4", "G", 63, "4/4"],
      ["Key: A, Tempo: 75, 4/4", "A", 75, "4/4"],
      ["Key of A      68 BPM      4/4 Time", "A", 68, "4/4"],
      ["C • 81 BPM • 4/4", "C", 81, "4/4"],
      ["4/4 @ 140", null, 140, "4/4"],
      ["Key: C · Tempo: 70 · Time: 4/4", "C", 70, "4/4"],
    ] as const) {
      const meta = { key: null, tempo: null, time: null } as Record<string, unknown>;
      expect(parseFactsLine(line, meta as never), line).toBe(true);
      expect(meta, line).toMatchObject({ key, tempo, time });
    }
    expect(parseFactsLine("The key to my heart is You", {} as never)).toBe(false);
  });

  it("finds CCLI numbers mid-sentence and wrapped, and tells the licence apart", () => {
    expect(scanCcli("Copyright 2012 United Pursuit Music (ASCAP) CCLI# 6494911")).toEqual({ song: "6494911", license: null });
    expect(scanCcli("Music). CCLI Song No.\n7038281. This chart is the property of Carmel Baptist. CCLI No. 83734.")).toEqual({
      song: "7038281", license: "83734",
    });
    expect(scanCcli("CCLI Song # 7117726\nCCLI License # 11262876")).toEqual({ song: "7117726", license: "11262876" });
  });

  it("reads a SongSelect heading and keeps its footer out of the lyrics", () => {
    const [s] = parseTextChart(
      [
        "Goodness Of God",
        "Jenn Johnson | Ed Cash | Jason Ingram",
        "(based on the recording by Jenn Johnson)",
        "Key - G | Tempo - 63 | Time - 4/4",
        "",
        "VERSE 1",
        "I love You [G]Lord",
        "",
        "TAG",
        "I will [C2]sing",
        "CCLI Song # 7117726",
        "© 2018 Capitol CMG Paragon | Bethel Music Publishing",
        "For use solely w ith the SongSelect® Terms of Use. All rights reserved. w w w .ccli.com",
        "CCLI License # 11262876",
      ].join("\n"),
      "x",
    );
    expect(s).toMatchObject({
      title: "Goodness Of God",
      artist: "Jenn Johnson, Ed Cash, Jason Ingram",
      ccli: "7117726",
      ccliLicense: "11262876",
      copyright: "2018 Capitol CMG Paragon | Bethel Music Publishing",
    });
    expect(s!.chart).toMatchObject({ key: "G", tempo: 63, time: "4/4" });
    expect(s!.chart.sections.map((x) => x.label)).toEqual(["Verse 1", "Tag"]);
  });

  it("uses a roadmap line as the play order", () => {
    expect(roadmapLine("Intro, V1, C, V2, Cx2, B")).toEqual(["Intro", "Verse 1", "Chorus", "Verse 2", "Chorus", "Chorus", "Bridge"]);
    expect(roadmapLine("Verse 1 > Verse 2 > Chorus 1 (2x)")).toEqual(["Verse 1", "Verse 2", "Chorus 1", "Chorus 1"]);
    expect(roadmapLine("We love You, Lord, and we sing along")).toBeNull();
    const [s] = parseTextChart("Song\n\nV1, C, V2, C\n\nVerse 1\nOne\n\nChorus\nSing\n\nVerse 2\nTwo", "x");
    expect(s!.order).toEqual(["Verse 1", "Chorus", "Verse 2", "Chorus"]);
  });
});

describe("tidying lyric lines", () => {
  it("rejoins split syllables and closes gaps, keeping chords on their syllables", () => {
    const out = tidyLyricLine({
      text: "All sur - rendered now    I rise",
      chords: [{ pos: 10, chord: "C2" }, { pos: 26, chord: "D" }],
    });
    expect(out.text).toBe("All surrendered now I rise");
    expect(out.chords).toEqual([{ pos: 7, chord: "C2" }, { pos: 20, chord: "D" }]);
    expect(out.text.slice(7, 10)).toBe("ren");
    expect(out.text.slice(20, 21)).toBe("I");
  });

  it("leaves real dashes between interjections alone", () => {
    expect(tidyLyricLine({ text: "Whoa - oh - oh", chords: [] }).text).toBe("Whoa - oh - oh");
  });
});

describe("writer names", () => {
  it("joins kerning splits and separators", () => {
    const [s] = parseTextChart("Build My Life\nPassion Band/Brett Younker\n\nVerse 1\nWorthy", "x");
    expect(s!.artist).toBe("Passion Band, Brett Younker");
    const [t] = parseTextChart("Goodness Of God\nJenn Johnson | Ed C ash\n\nVerse 1\nI love You Lord", "x");
    expect(t!.artist).toBe("Jenn Johnson, Ed Cash");
  });
});

describe("notation lead sheet PDFs", () => {
  it("extracts title, artist, tempo, CCLI, rejoined syllables, and chords from multi-verse staff systems", () => {
    const page1: PdfTextItem[] = [
      run("Harvest Light", 228, 713.7, 15.4),
      run("(as published by Example Music Publishing)", 252, 706.3, 6.1),
      run("Words and Music by", 510, 700.8, 6.1),
      run("Test Writer and Sample Author", 430, 693.8, 8.2),
      run("VERSE", 88, 677.9, 10.2),
      run("D", 108, 661.9, 10.2),
      run("G", 439, 661.9, 10.2),
      run("Bm", 509, 661.9, 10.2),
      run("\uE1D5", 55, 648.2, 10.9),
      run("=68", 60, 648.2, 10.2),
      // Verse 1 lyric row
      run("Sun", 117, 593.8, 10.2),
      run("lit", 145, 593.8, 10.2),
      run("the", 192, 593.8, 10.2),
      run("hill", 214, 593.8, 10.2),
      run("at", 248, 593.8, 10.2),
      run("the", 266, 593.8, 10.2),
      run("glo", 294, 593.8, 10.2),
      run("ri", 317, 593.8, 10.2),
      run("ous,", 349, 593.8, 10.2),
      run("red", 383, 593.8, 10.2),
      run("sky", 408, 593.8, 10.2),
      run("and", 436, 593.8, 10.2),
      run("the", 464, 593.8, 10.2),
      run("cool", 507, 593.8, 10.2),
      run("West", 537, 593.8, 10.2),
      // Verse 2 lyric row
      run("We", 145, 579.1, 7.7),
      run("did", 168, 579.1, 7.7),
      run("n't", 184, 579.1, 7.7),
      run("need", 214, 579.1, 10.2),
      run("heav", 248, 579.1, 10.2),
      run("en", 274, 579.1, 10.2),
      run("with", 294, 579.1, 10.2),
      run("out", 317, 579.1, 10.2),
      run("you,", 349, 579.1, 10.2),
      // Second system with SMuFL sharp in A/C#
      run("A", 87, 557.4, 10.2),
      run("Bm", 152, 557.4, 10.2),
      run("A/C", 307, 557.4, 10.2),
      run("\uEA66", 324, 557.4, 18.2),
      run("D", 360, 557.4, 10.2),
      run("4", 51, 552.1, 8.2),
      run("Day.", 85, 503.8, 10.2),
      run("Our", 157, 503.8, 10.2),
      run("gar", 187, 503.8, 10.2),
      run("den", 208, 503.8, 10.2),
      run("glo", 232, 503.8, 10.2),
      run("ry", 263, 503.8, 10.2),
      run("in", 293, 503.8, 10.2),
      run("har", 318, 503.8, 10.2),
      run("mo", 344, 503.8, 10.2),
      run("ny,", 375, 503.8, 10.2),
      run("down.", 85, 489.1, 10.2),
      run("Our", 164, 489.1, 10.2),
      run("road", 187, 489.1, 10.2),
      run("was", 208, 489.1, 10.2),
      run("long,", 232, 489.1, 10.2),
      run("CCLI Song # 1234567", 283, 85.5, 5.1),
      run("© 2016 Example Music Publishing", 261, 80.9, 5.1),
    ];
    const hyphens = [
      { x: 310.3, y: 596.1 },
      { x: 339.8, y: 596.1 },
      { x: 314.4, y: 581.4 },
      { x: 203.9, y: 506.1 },
      { x: 254.0, y: 506.1 },
      { x: 337.3, y: 506.1 },
      { x: 361.7, y: 506.1 },
    ];
    const text = pagesToChartText([page1], [hyphens]);
    const [song] = parseTextChart(text, "Harvest Light");
    expect(song).toMatchObject({
      title: "Harvest Light",
      artist: "Test Writer and Sample Author",
      ccli: "1234567",
    });
    expect(song!.chart.tempo).toBe(68);
    expect(song!.chart.sections.map((s) => s.label)).toEqual(["Verse 1", "Verse 2"]);
    expect(song!.chart.sections[0]!.stanzas[0]![0]!.text).toBe(
      "Sun lit the hill at the glorious, red sky and the cool West",
    );
    expect(song!.chart.sections[0]!.stanzas[0]![1]!.text).toBe(
      "Day. Our garden glory in harmony,",
    );
    expect(song!.chart.sections[0]!.stanzas[0]![1]!.chords.map((c) => c.chord)).toEqual([
      "A",
      "Bm",
      "A/C#",
      "D",
    ]);
  });

  it("merges SongSelect superscript chord extensions (A + 2sus -> A2sus) and rejoins stretched hyphenated words (Al - le - lu - ia -> Alleluia)", () => {
    const page: PdfTextItem[] = [
      run("Agnus Dei", 39.2, 742.6, 18.6),
      run("Michael W. Smith", 39.2, 731.8, 7.8),
      run("Key -", 39.2, 720.4, 10.2),
      run("A", 66.8, 720.4, 10.2),
      run("|", 76.4, 720.4, 10.2),
      run("Tempo - 66 BPM | Time - 4/4", 84.2, 720.4, 10.2),
      run("VERSE", 39.2, 688.6, 15.0),
      // Chord row with raised "2sus" extension
      run("A", 61.4, 666.4, 15.0),
      run("A", 88.4, 666.4, 15.0),
      run("2sus", 98.0, 670.6, 12.0),
      run("A", 141.8, 666.4, 15.0),
      run("D/A", 170.0, 666.4, 15.0),
      run("A", 215.7, 666.4, 15.0),
      // Stretched hyphenated lyric row
      run("Al", 61.4, 654.4, 15.0),
      run("-", 88.4, 654.4, 15.0),
      run("le", 141.8, 654.4, 15.0),
      run("-", 160.4, 654.4, 15.0),
      run("lu", 170.0, 654.4, 15.0),
      run("-", 215.7, 654.4, 15.0),
      run("ia", 238.5, 654.4, 15.0),
    ];
    const text = pagesToChartText([page]);
    const [song] = parseTextChart(text, "Agnus Dei");
    expect(song!.title).toBe("Agnus Dei");
    expect(song!.chart.key).toBe("A");
    const firstLine = song!.chart.sections[0]!.stanzas[0]![0]!;
    expect(firstLine.text).toBe("Alleluia");
    expect(firstLine.chords.map((c) => c.chord)).toEqual([
      "A",
      "A2sus",
      "A",
      "D/A",
      "A",
    ]);
  });
});

describe("SongSelect sheet quirks (All Glory Be To Christ)", () => {
  const sheet = [
    "All Glory Be To Christ",
    "Dustin Kensrue",
    "Traditional Scottish Folk Tune",
    "Key - E | Tempo - 94 | Time - 4/4",
    "",
    "VERSE 1",
    "N.C.",
    "Though every tower falls to dust and every river dries",
    "",
    "VERSE 3",
    "F#       D#m/F#    G#m7/D#m   G#m7/C#       F#",
    "The sun that rises every day is making all things bright",
  ].join("\n");
  const [song] = parseTextChart(sheet, "fallback");
  const lyrics = song!.chart.sections.flatMap((s) => s.stanzas.flat().map((l) => l.text.trim())).filter(Boolean);

  it("keeps a third credit line before the key line out of the slides", () => {
    expect(song!.title).toBe("All Glory Be To Christ");
    expect(song!.artist).toBe("Dustin Kensrue");
    expect(song!.chart.sections.map((s) => s.label)).toEqual(["Verse 1", "Verse 3"]);
  });

  it("reads N.C. lines and minor-bass chords as the band's, not lyrics", () => {
    expect(lyrics).toEqual([
      "Though every tower falls to dust and every river dries",
      "The sun that rises every day is making all things bright",
    ]);
  });
});
