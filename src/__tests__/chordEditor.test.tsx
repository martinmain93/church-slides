import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChordLyrics, chordWords } from "@/components/band/ChordLyrics";
import { SongEditor } from "@/components/library/SongEditor";
import { SongPreview } from "@/components/library/SongPreview";
import { ConfirmProvider } from "@/lib/confirm";
import { ToastProvider } from "@/lib/toast";
import { keySignatureOptions, respellChord } from "@/lib/chords/chord";
import {
  diatonicChordsForKey,
  fromChordText,
  moveChordInBody,
  respellChartAndText,
  setInstrumentalChordsInBody,
  setWordChordInBody,
  stepTransposeChartAndText,
  toChordText,
  transposeChartAndText,
} from "@/lib/chords/editorText";
import { importChartFile } from "@/lib/chords/import";
import { chartToSong } from "@/lib/chords/import/toSong";

const [song] = importChartFile(
  "grace.txt",
  [
    "Amazing Grace", "", "Key: G", "", "Intro:", "G  D  G", "", "Verse 1",
    // Chords over "mazing", "sweet", "sound".
    " G".padEnd(18) + "C".padEnd(10) + "G",
    "Amazing grace how sweet the sound", "", "Chorus",
    "C".padEnd(14) + "G",
    "My chains are gone",
  ].join("\n"),
).songs;
const { body_text: body } = chartToSong(song!);

describe("chord text in the editor", () => {
  it("writes the chart's chords on lines above the lyrics, intro included", () => {
    const text = toChordText(body, song!.chart);
    expect(text).toContain("[Intro]\nG  D  G");
    expect(text).toContain(" G                C         G\nAmazing grace how sweet the sound");
    expect(text).toContain("C             G\nMy chains are gone");
  });

  it("splits back to exactly the original body", () => {
    const { body: back, chart } = fromChordText(toChordText(body, song!.chart), song!.chart);
    expect(back).toBe(body);
    expect(chart!.key).toBe("G");
    // …and the chart survives the round trip.
    expect(toChordText(body, chart)).toBe(toChordText(body, song!.chart));
  });

  it("reads two-line chords-over-lyrics typed into the editor", () => {
    const { body: b, chart } = fromChordText("[verse 1]\n      D\nHello there\n   Em\nmy friend", null);
    expect(b).toBe("[verse 1]\nHello there\nmy friend");
    expect(chart!.sections[0]!.stanzas[0]![0]).toEqual({ text: "Hello there", chords: [{ pos: 6, chord: "D" }] });
    expect(chart!.sections[0]!.stanzas[0]![1]).toEqual({ text: "my friend", chords: [{ pos: 3, chord: "Em" }] });
  });

  it("still reads inline [G] chords typed or pasted into the editor", () => {
    const { body: b, chart } = fromChordText("[verse 1]\nHello [D]there\nmy [Em]friend", null);
    expect(b).toBe("[verse 1]\nHello there\nmy friend");
    expect(chart!.sections[0]!.stanzas[0]![0]).toEqual({ text: "Hello there", chords: [{ pos: 6, chord: "D" }] });
  });

  it("keeps a chord line out of the body even mid-keystroke on a slash chord (G/)", () => {
    const { body: b, chart } = fromChordText("[verse 1]\nG/\nAmazing grace", null);
    expect(b).toBe("[verse 1]\nAmazing grace");
    expect(chart!.sections[0]!.stanzas[0]![0]).toEqual({ text: "Amazing grace", chords: [{ pos: 0, chord: "G" }] });
  });

  it("leaves a body with no chords alone and makes no chart", () => {
    expect(fromChordText("[chorus]\nJust words", null)).toEqual({ body: "[chorus]\nJust words", chart: null });
    expect(toChordText("[chorus]\nJust words", null)).toBe("[chorus]\nJust words");
  });
});

describe("interactive word-click chord placement in SongPreview", () => {
  it("places, updates, and removes chords by clicking words in the preview", () => {
    const plainBody = "[Verse 1]\nAmazing grace how sweet";
    const step1 = setWordChordInBody(plainBody, null, "Amazing grace how sweet", 0, 0, 8, 0, "G");
    expect(step1.chordText).toContain("G\nAmazing grace how sweet");
    expect(step1.chart).not.toBeNull();

    const step2 = setWordChordInBody(plainBody, step1.chart, "Amazing grace how sweet", 0, 18, 23, 18, "C");
    expect(step2.chordText).toContain("G                 C\nAmazing grace how sweet");

    const step3 = setWordChordInBody(plainBody, step2.chart, "Amazing grace how sweet", 0, 0, 8, 0, null);
    expect(step3.chordText).toContain("                  C\nAmazing grace how sweet");
  });

  it("opens the diatonic chord palette for the song's key when clicking a word", () => {
    expect(diatonicChordsForKey("C").slice(0, 6)).toEqual(["C", "Dm", "Em", "F", "G", "Am"]);
    const onSetWordChord = vi.fn();
    render(
      <SongPreview
        body={"[Verse 1]\nAmazing grace"}
        chart={null}
        chartKey="C"
        onSetWordChord={onSetWordChord}
      />,
    );
    const words = screen.getAllByTestId("chord-word-btn");
    fireEvent.click(words[0]!);
    expect(screen.getByTestId("chord-picker-popover")).toBeTruthy();
    // Clicking the same word again toggles the popover closed.
    fireEvent.mouseDown(words[0]!);
    fireEvent.click(words[0]!);
    expect(screen.queryByTestId("chord-picker-popover")).toBeNull();
    // Open again and select a quick chord.
    fireEvent.click(words[0]!);
    fireEvent.click(screen.getByTestId("chord-quick-Am"));
    expect(onSetWordChord).toHaveBeenCalledWith("Amazing grace", 0, 0, 8, 0, "Am");
  });

  it("moves a chord over an arbitrary letter in a word or to another word", () => {
    const plainBody = "[Verse 1]\nAmazing grace how sweet";
    const step1 = setWordChordInBody(plainBody, null, "Amazing grace how sweet", 0, 0, 8, 0, "G");
    // Move G from pos 0 ('A') to pos 3 ('z' of 'Amazing')
    const movedInWord = moveChordInBody(plainBody, step1.chart, "Amazing grace how sweet", 0, 0, 0, 3, "G");
    expect(movedInWord.chordText).toContain("   G\nAmazing grace how sweet");
    // Move G to pos 8 ('grace')
    const movedToNextWord = moveChordInBody(plainBody, movedInWord.chart, "Amazing grace how sweet", 0, 3, 0, 8, "G");
    expect(movedToNextWord.chordText).toContain("        G\nAmazing grace how sweet");
  });

  it("combines major and relative minor keys into 12 options and toggles sharps vs flats", () => {
    const flats = keySignatureOptions(true);
    const sharps = keySignatureOptions(false);
    expect(flats).toHaveLength(12);
    expect(sharps).toHaveLength(12);
    expect(flats[0]!.label).toBe("C (Am)");
    expect(flats[1]!.label).toBe("Db (Bbm)");
    expect(sharps[1]!.label).toBe("C# (A#m)");
    expect(respellChord("F#m7/C#", true)).toBe("Gbm7/Db");
    expect(respellChord("Gbm7/Db", false)).toBe("F#m7/C#");

    const { chart } = fromChordText("[Verse 1]\nF#        C#\nAmazing grace", { key: "F#", tempo: null, time: null });
    const asFlats = respellChartAndText("[Verse 1]\nAmazing grace", chart, true);
    expect(asFlats.chart?.key).toBe("Gb");
    expect(asFlats.chordText).toContain("Gb        Db\nAmazing grace");
  });

  it("supports syllable-level chord placement, keyboard 1-7 number shortcuts, Nashville input, and shortcut legend", () => {
    const onSetWordChord = vi.fn();
    render(
      <SongPreview
        body={"[Verse 1]\nAmazing grace"}
        chart={null}
        chartKey="G"
        onSetWordChord={onSetWordChord}
      />,
    );
    expect(screen.getByTestId("chord-shortcut-legend")).toBeTruthy();

    // Click "Amazing" -> opens popover with A · ma · zing syllable chips
    const words = screen.getAllByTestId("chord-word-btn");
    fireEvent.click(words[0]!);
    expect(screen.getByTestId("syllable-selector")).toBeTruthy();
    expect(screen.getByTestId("syllable-chip-0")).toHaveTextContent("A");
    expect(screen.getByTestId("syllable-chip-1")).toHaveTextContent("ma");
    expect(screen.getByTestId("syllable-chip-2")).toHaveTextContent("zing");

    // Select the 2nd syllable ("ma", start=1, end=3) and press "4" (IV in G = C)
    fireEvent.click(screen.getByTestId("syllable-chip-1"));
    fireEvent.keyDown(document, { key: "4" });
    expect(onSetWordChord).toHaveBeenCalledWith("Amazing grace", 0, 1, 3, 1, "C");

    // Open "grace" and type Nashville number "6m7" (vim7 in G = Em7)
    fireEvent.click(words[1]!);
    const input = screen.getByTestId("chord-custom-input");
    fireEvent.change(input, { target: { value: "6m7" } });
    expect(screen.getByTestId("nashville-preview")).toHaveTextContent("Em7");
    fireEvent.submit(input.closest("form")!);
    expect(onSetWordChord).toHaveBeenLastCalledWith("Amazing grace", 0, 8, 13, 8, "Em7");
  });

  it("transposes all chords when changing key or stepping semitones, and edits instrumental sections", () => {
    const plainBody = "[Verse 1]\nAmazing grace";
    const initial = fromChordText("[Intro]\nG  D  Em  C\n\n[Verse 1]\nG         C\nAmazing grace", {
      key: "G",
      tempo: null,
      time: null,
    });

    // Transpose from G -> A (+2 semitones)
    const inA = transposeChartAndText(plainBody, initial.chart, "A", false);
    expect(inA.chart?.key).toBe("A");
    expect(inA.chordText).toContain("[Intro]\nA  E  F#m  D");
    expect(inA.chordText).toContain("A         D\nAmazing grace");

    // Step transpose down 1 semitone (A -> Ab)
    const stepped = stepTransposeChartAndText(plainBody, inA.chart, -1);
    expect(stepped.nextKey).toBe("Ab");
    expect(stepped.chordText).toContain("[Intro]\nAb  Eb  Fm  Db");

    // Add a Turnaround after Verse 1 (slotIdx = 1)
    const withTurnaround = setInstrumentalChordsInBody(
      plainBody,
      inA.chart,
      1,
      -1,
      "Turnaround",
      ["F#m", "D", "A", "E"],
    );
    expect(withTurnaround.chordText).toContain("[Turnaround]\nF#m  D  A  E");
  });

  it("renders chords as Nashville Numbers when displayMode is 'numbers'", () => {
    const { chart } = fromChordText("[Verse 1]\nG         C    D/F#\nAmazing grace how sweet", {
      key: "G",
      tempo: null,
      time: null,
    });
    render(
      <SongPreview
        body={"[Verse 1]\nAmazing grace how sweet"}
        chart={chart}
        chartKey="G"
        displayMode="numbers"
      />,
    );
    const preview = screen.getByTestId("preview-chords");
    expect(preview.textContent).toContain("1");
    expect(preview.textContent).toContain("4");
    expect(preview.textContent).toContain("5/7");
  });

  it("jumps up and down across lyric lines with ArrowDown and ArrowUp", () => {
    const onSetWordChord = vi.fn();
    render(
      <SongPreview
        body={"[Verse 1]\nAmazing grace how sweet\nThat saved a wretch"}
        chart={null}
        chartKey="G"
        onSetWordChord={onSetWordChord}
      />,
    );
    const words = screen.getAllByTestId("chord-word-btn");
    // Click 2nd word on line 1 ("grace", start=8)
    fireEvent.click(words[1]!);
    expect(screen.getByTestId("chord-picker-popover")).toHaveTextContent("grace");

    // Press ArrowDown -> jumps to closest word on line 2 ("saved", start=5)
    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(screen.getByTestId("chord-picker-popover")).toHaveTextContent("saved");

    // Press ArrowUp -> jumps back to line 1 ("grace", start=8)
    fireEvent.keyDown(document, { key: "ArrowUp" });
    expect(screen.getByTestId("chord-picker-popover")).toHaveTextContent("grace");
  });

  it("renders 16:9 projector slides in non-chord mode and allows inline lyric editing in both modes", () => {
    const onChangeBody = vi.fn();
    const { rerender } = render(
      <SongPreview
        body={"[Verse 1]\nAmazing grace\n\n[Chorus]\nMy chains are gone"}
        chart={null}
        onChangeBody={onChangeBody}
      />,
    );

    const projectorCards = screen.getAllByTestId("preview-slide");
    expect(projectorCards).toHaveLength(2);
    expect(projectorCards[0]!.className).toContain("aspect-video");

    // Duplicate the first slide in 16:9 Projector mode
    const dupBtns = screen.getAllByRole("button", { name: /duplicate slide/i });
    fireEvent.click(dupBtns[0]!);
    expect(onChangeBody).toHaveBeenCalledWith(
      expect.stringContaining("[verse 1]\n\nAmazing grace\n\nAmazing grace"),
    );

    // Switch to Chords mode (Leader Sheet cards) and edit lyrics inline inside card 0
    const { chart } = fromChordText("[Verse 1]\nG         C\nAmazing grace", {
      key: "G",
      tempo: null,
      time: null,
    });
    rerender(
      <SongPreview
        body={"[Verse 1]\nAmazing grace"}
        chart={chart}
        chartKey="G"
        onSetWordChord={vi.fn()}
        onChangeBody={onChangeBody}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-lyrics-in-chords-btn-0"));
    const inlineArea = screen.getByTestId("inline-chord-lyrics-textarea");
    fireEvent.change(inlineArea, { target: { value: "Amazing grace how sweet" } });
    expect(onChangeBody).toHaveBeenLastCalledWith("[verse 1]\n\nAmazing grace how sweet\n");
  });
});

describe("chords above lyrics that wrap", () => {
  it("keeps each chord with its syllable and splits the line into words", () => {
    const words = chordWords("Amazing grace", [{ pos: 1, chord: "G" }, { pos: 8, chord: "C" }]);
    expect(words).toEqual([
      [{ chord: null, text: "A" }, { chord: "G", text: "mazing " }],
      [{ chord: "C", text: "grace" }],
    ]);
  });

  it("keeps a chord that sits past the end of the lyric", () => {
    const words = chordWords("be still", [{ pos: 8, chord: "D" }]);
    expect(words.flat().some((p) => p.chord === "D")).toBe(true);
  });

  it("renders one wrapping block per word, never a fixed-width row", () => {
    render(<ChordLyrics text="A long line of lyrics that would run off a phone" chords={[{ pos: 2, chord: "Gmaj7" }]} />);
    const line = screen.getByTestId("chord-line");
    expect(line.className).toContain("flex-wrap");
    expect(line.children.length).toBe(11);
    expect(line.textContent).toContain("Gmaj7");
  });
});

describe("SongEditor Fill from PDF / Chord Sheet button", () => {
  it("renders Fill from PDF next to Raw text and fills title, author, CCLI, key, lyrics, and chords", async () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );

    const fillBtn = screen.getByTestId("fill-from-pdf");
    const rawBtn = screen.getByTestId("toggle-raw-text");
    expect(fillBtn).toBeInTheDocument();
    expect(rawBtn).toBeInTheDocument();

    const chordProContent = [
      "{title: Sunrise}",
      "{artist: Test Writer}",
      "{key: G}",
      "{ccli: 7070345}",
      "",
      "{start_of_verse: Verse 1}",
      "[G]Sing of the sun [C2]rising over the sea",
      "{end_of_verse}",
    ].join("\n");

    const file = new File([chordProContent], "Sunrise-G.cho", {
      type: "text/plain",
    });

    const input = screen.getByTestId("fill-from-pdf-input");
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId("song-title")).toHaveValue("Sunrise");
    });
    expect(screen.getByTestId("song-author")).toHaveValue("Test Writer");
    expect(screen.getByTestId("song-ccli")).toHaveValue("7070345");
    expect(screen.getByTestId("chart-key")).toHaveTextContent("G");
    // Chords mode is automatically turned on and displays the placed chords
    expect(screen.getByTestId("toggle-chords")).toHaveTextContent("Hide chords");
    expect(screen.getByTestId("song-body")).toHaveValue(
      "[Verse 1]\nG               C2\nSing of the sun rising over the sea",
    );
  });
});

describe("SongEditor Suggest & Cursor-Tab Next-Edit Workflow", () => {
  it("mirrors chords from Verse 1 to Verse 2 via the Suggest button and Tab key, and supports Undo", async () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );

    // Turn on chords mode and paste a song where Verse 1 has chords and Verse 2 is un-charted
    fireEvent.click(screen.getByTestId("toggle-chords"));
    fireEvent.change(screen.getByTestId("song-body"), {
      target: {
        value: [
          "[Verse 1]",
          "G               C2",
          "Sing of the sun rising over the sea",
          "",
          "[Chorus]",
          "C2              G",
          "Faithful through the longest night",
          "",
          "[Verse 2]",
          "Jesus the name above every other name",
        ].join("\n"),
      },
    });

    // The Suggest button has no noisy count badge on the toolbar
    const suggestBtn = screen.getByTestId("suggest-btn");
    expect(suggestBtn).toBeInTheDocument();
    expect(screen.queryByTestId("suggest-count-badge")).not.toBeInTheDocument();

    // Click "Suggest" to open the Suggestion Review Bar & render ghost chord pills on Verse 2
    fireEvent.click(suggestBtn);
    expect(screen.getByTestId("suggestion-review-bar")).toBeInTheDocument();
    const ghostPills = screen.getAllByTestId("ghost-chord-pill");
    expect(ghostPills.length).toBe(2);
    expect(ghostPills[0]).toHaveTextContent("G");
    expect(ghostPills[1]).toHaveTextContent("C2");

    // Press Tab once -> accepts the first ghost chord (G on "Jesus" in Verse 2)
    fireEvent.keyDown(window, { key: "Tab" });
    await waitFor(() => {
      // The remaining suggestion on Verse 2 is C2 on "every"
      const remainingPills = screen.getAllByTestId("ghost-chord-pill");
      expect(remainingPills[0]).toHaveTextContent("C2");
    });

    // Click "Accept all" for the remaining suggestions (C2 on Verse 2 + Chorus repeat in Arrangement Order)
    fireEvent.click(screen.getByTestId("accept-all-suggestions-btn"));

    await waitFor(() => {
      expect(screen.queryByTestId("suggestion-review-bar")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("song-body").getAttribute("value") ?? (screen.getByTestId("song-body") as HTMLTextAreaElement).value).toContain(
      "[Verse 2]\nG              C2\nJesus the name above every other name",
    );

    // Undo button restores the previous state
    const undoBtn = screen.getByTestId("undo-suggestion-btn");
    expect(undoBtn).toBeInTheDocument();
    fireEvent.click(undoBtn);
    await waitFor(() => {
      expect(screen.queryByTestId("undo-suggestion-btn")).not.toBeInTheDocument();
    });
  });

  it("does not copy a title-line chord onto the same verse or the next verse", async () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );

    fireEvent.click(screen.getByTestId("toggle-chords"));
    fireEvent.change(screen.getByTestId("song-body"), {
      target: {
        value: [
          "[Verse 1]",
          "Blessed be Your name",
          "In the land where the barley grows",
          "Blessed be Your name",
          "",
          "[Verse 2]",
          "Blessed be Your name",
        ].join("\n"),
      },
    });

    const wordBtns = screen.getAllByTestId("chord-word-btn");
    fireEvent.click(wordBtns[0]!);
    fireEvent.click(screen.getByTestId("chord-quick-G"));

    expect(screen.queryAllByTestId("ghost-chord-pill")).toHaveLength(0);
  });

  it("reactively predicts the next chord in bright yellow and later chords faded, without banner noise", async () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );

    fireEvent.click(screen.getByTestId("toggle-chords"));
    fireEvent.change(screen.getByTestId("song-body"), {
      target: {
        value: [
          "[Chorus]",
          "Alleluia, Alleluia",
          "Holy, Holy",
          "",
          "[Chorus]",
          "Alleluia, Alleluia",
          "Worthy is the Lamb",
        ].join("\n"),
      },
    });

    // Chart both words on line 1 so the repeated line has a current suggestion and a later one
    const wordBtns = screen.getAllByTestId("chord-word-btn");
    fireEvent.click(wordBtns[0]!);
    fireEvent.click(screen.getByTestId("chord-quick-G"));
    fireEvent.click(wordBtns[1]!);
    fireEvent.click(screen.getByTestId("chord-quick-C"));

    // Current suggestion is the bright pill (Tab). Later chords stay visible but faded.
    // Nothing is guessed onto "For the Lord God Almighty", and there is no review banner.
    await waitFor(() => {
      expect(screen.getAllByTestId("ghost-chord-pill")).toHaveLength(2);
    });
    expect(screen.queryByTestId("suggestion-review-bar")).not.toBeInTheDocument();
    const pills = screen.getAllByTestId("ghost-chord-pill");
    const active = pills.filter((el) => el.getAttribute("data-active-suggestion") === "true");
    const faded = pills.filter((el) => el.getAttribute("data-active-suggestion") === "false");
    expect(active).toHaveLength(1);
    expect(faded).toHaveLength(1);
    expect(active[0]).toHaveTextContent("G");
    expect(active[0]).toHaveTextContent("Tab");
    expect(faded[0]).toHaveTextContent("C");
    expect(faded[0]).not.toHaveTextContent("Tab");

    // Pressing Tab accepts only the bright current chord on line 3
    fireEvent.keyDown(window, { key: "Tab" });
    await waitFor(() => {
      const val = (screen.getByTestId("song-body") as HTMLTextAreaElement).value;
      expect(val).toContain(
        "G         C\nAlleluia, Alleluia\nHoly, Holy\n\n[Chorus]\nG\nAlleluia, Alleluia",
      );
    });

    // The faded chord steps up to become the bright current suggestion
    await waitFor(() => {
      const remaining = screen.getAllByTestId("ghost-chord-pill");
      expect(remaining).toHaveLength(1);
      expect(remaining[0]).toHaveAttribute("data-active-suggestion", "true");
      expect(remaining[0]).toHaveTextContent("C");
    });
  });

  it("suggests OCR hyphenation cleanup, and leaves splitting to the slide's scissors", async () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );

    // Paste a 4-line slide with a hyphenated OCR word ("Wor-thy")
    fireEvent.change(screen.getByTestId("song-body"), {
      target: {
        value: [
          "[Verse 1]",
          "Morn-ing light upon the hills we wake and sing,",
          "Every valley full of praise the rivers bring,",
          "Every field and every tree we pause and breathe,",
          "We rest in You.",
        ].join("\n"),
      },
    });

    fireEvent.click(screen.getByTestId("suggest-btn"));
    expect(screen.getByTestId("suggestion-review-bar")).toBeInTheDocument();
    // One way to split a slide: the scissors on it, not a suggestion too.
    expect(screen.queryByTestId("ghost-slide-split-line")).toBeNull();

    // Accept the cleanup -> "Morn-ing" becomes "Morning"; the slide stays whole
    fireEvent.click(screen.getByTestId("projector-ghost-suggestion-btn"));

    await waitFor(() => {
      expect((screen.getByTestId("song-body") as HTMLTextAreaElement).value).toContain("Morning light");
    });
    expect(screen.getAllByTestId("preview-slide").length).toBe(1);
    const val = (screen.getByTestId("song-body") as HTMLTextAreaElement).value;
    expect(val).toContain("Morning light upon the hills");
    expect(val).not.toContain("Wor-thy");
  });

  it("supports multiple chords across a single word's syllables without spilling into trailing chords, plus passing chords and discrete trailing chord pills", () => {
    const plainBody = "[Verse 1]\nAlleluia";
    // Place A + A2sus on "Al" (0..2), A on "le" (2..4), D/A on "lu" (4..6), A on "ia" (6..8)
    let state = setWordChordInBody(plainBody, null, "Alleluia", 0, 0, 2, 0, "A A2sus");
    state = setWordChordInBody(plainBody, state.chart, "Alleluia", 0, 2, 4, 2, "A");
    state = setWordChordInBody(plainBody, state.chart, "Alleluia", 0, 4, 6, 4, "D/A");
    state = setWordChordInBody(plainBody, state.chart, "Alleluia", 0, 6, 8, 6, "A");

    // Add two discrete trailing chords after "Alleluia" (at pos >= 8)
    state = setWordChordInBody(plainBody, state.chart, "Alleluia", 0, 9, 29, 9, "E/A");
    state = setWordChordInBody(plainBody, state.chart, "Alleluia", 0, 12, 32, 12, "D/A");

    const onSetWordChord = vi.fn();
    render(
      <SongPreview
        body={plainBody}
        chart={state.chart}
        chartKey="A"
        onSetWordChord={onSetWordChord}
      />,
    );

    // All 5 in-word chords stay inside "Alleluia" (4 syllable segments)
    const inWordBadges = screen.getAllByTestId("chord-badge").map((el) => el.textContent);
    expect(inWordBadges).toEqual(["A A2sus", "A", "D/A", "A"]);

    // The 2 trailing chords render as discrete individual pills plus a "+" add button
    const trailingBadges = screen.getAllByTestId("trailing-chord-badge").map((el) => el.textContent);
    expect(trailingBadges).toEqual(["E/A", "D/A"]);
    expect(screen.getByTestId("trailing-chord-add-btn")).toBeInTheDocument();
  });

  it("glows only the slides an AI take just changed", () => {
    const body = "[verse 1]\nAmazing grace\n\n[chorus]\nMy chains are gone";
    const { rerender } = render(
      <SongPreview body={body} onSetWordChord={() => {}} aiPulse={{ token: 1, slides: [1] }} />,
    );
    const cards = screen.getAllByTestId("preview-slide");
    expect(cards[0]).not.toHaveAttribute("data-ai-change", "true");
    expect(cards[1]).toHaveAttribute("data-ai-change", "true");
    expect(cards[1]?.querySelector(".ai-change-glow")).not.toBeNull();

    rerender(<SongPreview body={body} onSetWordChord={() => {}} aiPulse={null} />);
    expect(screen.getAllByTestId("preview-slide").every((card) => !card.hasAttribute("data-ai-change"))).toBe(true);
  });
});

describe("SongEditor tempo and time signature", () => {
  it("saves a tempo and time signature on a song with no chords, without a chords badge", async () => {
    const { listChartMeta, listSongs } = await import("@/lib/tauri");
    render(
      <ToastProvider>
        <ConfirmProvider>
          <SongEditor songId={null} onClose={vi.fn()} />
        </ConfirmProvider>
      </ToastProvider>,
    );
    fireEvent.change(screen.getByTestId("song-title"), { target: { value: "Tempo Test Song" } });
    fireEvent.change(screen.getByTestId("song-body"), { target: { value: "[verse 1]\nJust words here" } });
    fireEvent.click(screen.getByTestId("toggle-chords"));
    fireEvent.change(screen.getByTestId("chart-tempo"), { target: { value: "128" } });
    fireEvent.change(screen.getByTestId("chart-time"), { target: { value: "6/8" } });

    await waitFor(
      async () => {
        const id = (await listSongs()).find((s) => s.title === "Tempo Test Song")?.id;
        expect(id).toBeDefined();
        expect((await listChartMeta())[id!]).toEqual({ key: null, tempo: 128, time: "6/8", has_chords: false });
      },
      { timeout: 4000 },
    );
  });
});
