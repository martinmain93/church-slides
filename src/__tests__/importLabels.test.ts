// @vitest-environment node — jsdom's File has no arrayBuffer(); Node's does.
import { describe, expect, it, vi } from "vitest";

import { runAssistantImport, type ImportDeps } from "@/lib/assistantImport";
import { restoreLigatures } from "@/lib/chords/import/pdf";
import { planImport, toImportItem } from "@/lib/chords/import/plan";
import { relabelFromChart } from "@/lib/chords/import/relabel";
import { parseTextChart } from "@/lib/chords/import/text";
import { chartToSong } from "@/lib/chords/import/toSong";
import type { ChartImportItem, SongRow } from "@/lib/tauri";

const tags = (body: string) => body.match(/^\[[^\]]+\]/gm) ?? [];

function read(text: string) {
  const song = parseTextChart(text, "Fallback")[0]!;
  return { song, ...chartToSong(song) };
}

function libSong(id: string, title: string, body = "[v]\nx", ccli: string | null = null): SongRow {
  return {
    id, title, ccli, body_text: body, author: null, copyright: null,
    default_tempo_wps: null, slide_order: null, created_at: 0, updated_at: 0,
  };
}

describe("section labels from chord sheets", () => {
  it("reads SongSelect's lettered variants as one section", () => {
    const { body_text, slide_order } = read(`Gratitude

VERSE 1
All my words are small, I have nothing new

CHORUS 1A
So I raise my voice and sing to You again and again

BRIDGE 1A
Come on, my soul

CHORUS 1B
So I raise my voice and sing to You again and again
`);
    expect(tags(body_text)).toEqual(["[Verse 1]", "[Chorus 1]", "[Bridge 1]"]);
    expect(slide_order).toBe("V, C1, B, C1");
  });

  it("calls a sung interlude a tag, not an instrumental", () => {
    const { body_text } = read(`All Who Are Thirsty

CHORUS
Holy Spirit come

INTERLUDE
As deep cries out to deep
`);
    expect(tags(body_text)).toEqual(["[Chorus]", "[Tag]"]);
  });

  it("names the stanzas of a sheet with no headings when its shape says what they are", () => {
    const hymn = read(`Holy, Holy, Holy

Holy, holy, holy! Lord God Almighty!
Early in the morning our song shall rise to Thee;
Holy, holy, holy! Merciful and mighty!

Holy, holy, holy! All the saints adore Thee,
Casting down their golden crowns around the glassy sea;
Cherubim and seraphim falling down before Thee,

Holy, holy, holy! Though the darkness hide Thee,
Though the eye of sinful man Thy glory may not see,
Only Thou art holy; there is none beside Thee,
`);
    expect(tags(hymn.body_text)).toEqual(["[Verse 1]", "[Verse 2]", "[Verse 3]"]);

    const refrain = read(`Some Song

First verse line
Another verse line

Sung again and again
The same each time

Second verse line
More of the verse

Sung again and again
The same each time
`);
    expect(tags(refrain.body_text)).toEqual(["[Verse 1]", "[Chorus]", "[Verse 2]"]);
    expect(refrain.slide_order).toBe("V1, C1, V2, C1");

    // Nothing repeats and the stanzas differ in length: one verse, two slides.
    const plain = read(`Short Song

One line here
And another

Then three lines
Of something
Else entirely
`);
    expect(tags(plain.body_text)).toEqual(["[Verse]"]);
  });

  it("splits a performer's name off the title line", () => {
    expect(read("Worthy of it All – David Brymer\n\nAll the hills and valleys\n").song).toMatchObject({
      title: "Worthy of it All",
      artist: "David Brymer",
    });
    // A subtitle is not a person.
    expect(read("Amazing Grace – My Chains Are Gone\n\nAmazing grace\n").song.title).toBe(
      "Amazing Grace – My Chains Are Gone",
    );
  });

  it("fills in ligatures a PDF dropped", () => {
    expect(restoreLigatures("our e\u0000orts stand").text).toBe("our efforts stand");
    expect(restoreLigatures("For the di\u0000erent ways, a re - \u0000ection, uni\u0000ed").text).toBe(
      "For the different ways, a re - flection, unified",
    );
    expect(restoreLigatures("O precious is the\u0000ow").text).toBe("O precious is the flow");
    const odd = restoreLigatures("zz\u0000qq");
    expect(odd.guessed).toEqual(["zzfiqq"]);
  });
});

describe("matching a sheet to the library", () => {
  it("trusts an exact title over a CCLI number that belongs to an unrelated song", () => {
    const sheet = parseTextChart("I surrender all\nCCLI Song # 2299325\n\nVerse 1\nAll to Jesus I surrender\n", "x");
    const library = [
      libSong("a", "Jesus All For Jesus", "[v]\nx", "2299325"),
      libSong("b", "I Surrender All"),
    ];
    const [row] = planImport([{ fileName: "isa.pdf", songs: sheet, error: null }], library);
    expect(row!.match?.id).toBe("b");
    expect(row!.matchNote).toContain("Jesus All For Jesus");
    // …and the disputed number isn't copied onto it.
    expect(toImportItem(row!, { kind: "attach", songId: "b" }, library).fill_metadata?.ccli).toBeNull();
  });
});

describe("naming an existing song's sections from a chart", () => {
  const chart = parseTextChart(`Steady Ground

VERSE 1
Morning light upon the hills we wake and sing
Every valley full of praise the rivers bring
Every field and every tree we pause and breathe
We rest in You

CHORUS
Faithful through the longest night
Shelter in the storm
Keep my heart in quiet trust

BRIDGE
I will set my hope on steady ground
Every promise holds
`, "x")[0]!;

  it("merges slide-sized verses into the chart's sections and keeps every word", () => {
    const body = `[verse 1]
Morning light upon the hills
we wake and sing

[verse 2]
Every field and every tree
We rest in you

[verse 3]
Faithful through the longest night

[verse 4]
Shelter in the storm
Keep my heart in quiet trust

[verse 5]
I will set my hope
On steady ground

[verse 6]
Faithful through the longest night

[verse 7]
Shelter in the storm
Keep my heart in quiet trust`;
    const out = relabelFromChart({ body_text: body, slide_order: null }, chart)!;
    expect(tags(out.body_text)).toEqual(["[Verse]", "[Chorus]", "[Bridge]"]);
    expect(out.slide_order).toBe("V, C1, B, C1");
    expect(out.body_text).toContain("Morning light upon the hills\nwe wake and sing\n\nEvery field and every tree");
  });

  it("leaves a song that already has real labels alone", () => {
    const body = "[Verse 1]\nMorning light upon the hills\n\n[Chorus]\nFaithful through the longest night";
    expect(relabelFromChart({ body_text: body, slide_order: null }, chart)).toBeNull();
  });

  it("leaves a song alone when the chart is a different song", () => {
    const body = "[verse 1]\nAmazing grace how sweet the sound\n\n[verse 2]\nThat saved a wretch like me";
    expect(relabelFromChart({ body_text: body, slide_order: null }, chart)).toBeNull();
  });
});

describe("assistant imports", () => {
  it("attaches to the song the assistant named, and relabels it", async () => {
    const library = [
      libSong("dup", "Worthy of it All"),
      libSong("keep", "Worthy Of It All (setlist copy)", "[verse 1]\nAll the hills and valleys\n\n[verse 2]\nYou are faithful through it all"),
    ];
    const saved: ChartImportItem[][] = [];
    const deps: ImportDeps = {
      readFile: async () => new TextEncoder().encode(`Worthy of it All

VERSE
[A]All the hills and valleys

CHORUS
You are [E]faithful through it all
`).buffer as ArrayBuffer,
      listSongs: async () => library,
      importCharts: vi.fn(async (items: ChartImportItem[]) => {
        saved.push(items);
        return { songs_created: 0, charts_attached: items.length, song_ids: items.map((i) => i.existing_song_id!) };
      }),
    };
    const report = await runAssistantImport(
      { id: "r", dry_run: false, files: [{ path: "/s/w.txt", name: "w.txt" }], attach_to: { "/s/w.txt": "keep" } },
      deps,
    );
    expect(report.songs[0]).toMatchObject({ outcome: "chords_added_to_existing", song_id: "keep" });
    expect(report.songs[0]!.reason).toContain("sections named from this chart");
    expect(saved[0]![0]!.relabel?.body_text).toBe("[Verse]\nAll the hills and valleys\n\n[Chorus]\nYou are faithful through it all");
  });
});
