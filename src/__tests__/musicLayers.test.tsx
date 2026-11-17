import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_MELODY,
  DEFAULT_PITCH_PARAMS,
  MusicLayers,
  chordAt,
  covering,
  describeAt,
  displayMelody,
  foldMidi,
  noteName,
  pulseAt,
  visiblePitch,
} from "@/components/recordings/MusicLayers";
import type { InspectMusicResponse } from "@/types/generated";

describe("music layer readouts", () => {
  it("reads the pulse in effect at the playhead, not a later one", () => {
    const pulse = [
      { t_seconds: 1, bpm: 96 },
      { t_seconds: 5, bpm: 120 },
      { t_seconds: 9, bpm: 140 },
    ];
    expect(pulseAt(pulse, 0)).toBeNull();
    expect(pulseAt(pulse, 4.9)).toBe(96);
    expect(pulseAt(pulse, 5)).toBe(120);
    expect(pulseAt(pulse, 12)).toBe(140);
  });

  it("names the chord covering the playhead", () => {
    const chords = [
      { name: "G", t_start: 0, t_end: 4 },
      { name: "D", t_start: 4, t_end: 8 },
    ];
    expect(chordAt(chords, 3.9)).toBe("G");
    expect(chordAt(chords, 4)).toBe("D");
    expect(chordAt(chords, 8)).toBeNull();
  });

  it("keeps the pulse point on each side of a zoomed window", () => {
    const pulse = [
      { t_seconds: 0, bpm: 60 },
      { t_seconds: 0.5, bpm: 70 },
      { t_seconds: 1.0, bpm: 80 },
      { t_seconds: 1.5, bpm: 90 },
    ];
    const shown = covering(pulse, { start: 0.6, end: 1.1, span: 0.5 });
    expect(shown.map((p) => p.t_seconds)).toEqual([0.5, 1.0, 1.5]);
  });

  it("names a midi note and the chord under the pointer", () => {
    expect(noteName(60)).toBe("C4");
    expect(noteName(67)).toBe("G4");
    const lines = describeAt(
      "chords",
      2,
      {
        pulse: [],
        beats: [],
        downbeats: [],
        chords: [{ name: "G", t_start: 0, t_end: 4 }],
        pitch: [],
        notes: [],
        onsets: [],
      },
    );
    expect(lines[0]).toBe("G");
    expect(lines[1]).toContain("0:00.0");
    expect(lines[1]).toContain("0:04.0");
  });

  it("offers solo once a band row is open", () => {
    const data: InspectMusicResponse = {
      kind: "inspect_music_response",
      duration_seconds: 10,
      beat_backend: "beat_this",
      chord_backend: "btc",
      pitch_backend: "basic_pitch",
      beats: [0.5],
      downbeats: [0.5],
      chords: [{ name: "G", t_start: 0, t_end: 10 }],
      pulse: [{ t_seconds: 1, bpm: 96, lock: 0.7 }],
      onsets: [{ band: "kick", hz_lo: 30, hz_hi: 90, times: [0.5] }],
      pitch: [],
      notes: [],
    };
    class Observer {
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", Observer);
    render(
      <MusicLayers
        data={data}
        view={{ start: 0, end: 10, span: 10 }}
        playhead={1}
        status="ready"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "kick" }));
    expect(screen.getByRole("button", { name: "solo" })).toBeInTheDocument();
  });

  it("shows the pulse and beat model under the scrubber", () => {
    const data: InspectMusicResponse = {
      kind: "inspect_music_response",
      duration_seconds: 10,
      beat_backend: "beat_this",
      chord_backend: "btc",
      pitch_backend: "basic_pitch",
      beats: [0.5, 1.5],
      downbeats: [0.5],
      chords: [{ name: "G", t_start: 0, t_end: 10 }],
      pulse: [{ t_seconds: 1, bpm: 96, lock: 0.7 }],
      onsets: [{ band: "kick", hz_lo: 30, hz_hi: 90, times: [0.5, 1.5] }],
      pitch: [{ t_seconds: 1, midi: 67, confidence: 0.5 }],
      notes: [{ t_start: 0.8, t_end: 1.6, midi: 67, confidence: 0.6 }],
    };
    class Observer {
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", Observer);
    render(
      <MusicLayers
        data={data}
        view={{ start: 0, end: 10, span: 10 }}
        playhead={2}
        status="ready"
      />,
    );
    expect(screen.getByText(/96 BPM/)).toBeInTheDocument();
    expect(screen.getByText(/Beat This/)).toBeInTheDocument();
    expect(screen.getByText(/Basic Pitch/)).toBeInTheDocument();
    expect(screen.getByText(/onsets kick/)).toBeInTheDocument();
    expect(document.querySelector("canvas")).not.toBeNull();
  });

  it("names which stem each detector is reading", () => {
    const data: InspectMusicResponse = {
      kind: "inspect_music_response",
      source: "stems",
      duration_seconds: 4,
      onsets: [{ band: "vocals", hz_lo: 0, hz_hi: 0, times: [1] }],
    };
    class Observer {
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", Observer);
    render(
      <MusicLayers
        data={data}
        view={{ start: 0, end: 4, span: 4 }}
        playhead={1}
        status="ready"
      />,
    );
    expect(screen.getByText(/beats on the mix/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "vocals" }));
    expect(screen.getByRole("button", { name: "solo" })).toBeInTheDocument();
  });

  it("hides pitch the sliders have cut, without touching the stored points", () => {
    const pitch = [
      { t_seconds: 0, midi: 60, confidence: 0.1 },
      { t_seconds: 0.1, midi: 62, confidence: 0.4 },
    ];
    const notes = [
      { t_start: 0, t_end: 0.05, midi: 60, confidence: 0.9 },
      { t_start: 1, t_end: 1.2, midi: 64, confidence: 0.2 },
      { t_start: 2, t_end: 2.4, midi: 67, confidence: 0.8 },
    ];
    const shown = visiblePitch(pitch, notes, DEFAULT_PITCH_PARAMS);
    expect(shown.pitch.map((p) => p.midi)).toEqual([62]);
    expect(shown.notes.map((n) => n.midi)).toEqual([67]);
    const open = visiblePitch(pitch, notes, {
      ...DEFAULT_PITCH_PARAMS,
      contour: 0.05,
      note: 0.15,
      minNote: 0.04,
    });
    expect(open.pitch).toHaveLength(2);
    expect(open.notes).toHaveLength(3);
  });

  it("names the rhythmic slot under the pointer", () => {
    const lines = describeAt(
      "onset",
      0.5,
      {
        pulse: [],
        beats: [0, 0.5, 1],
        downbeats: [0],
        chords: [],
        pitch: [],
        notes: [],
        onsets: [{ band: "kick", hz_lo: 30, hz_hi: 90, times: [0.5] }],
        rhythm: [
          {
            band: "kick",
            t: 0.5,
            phase: 0,
            kind: "quarter",
            error_ms: 4,
            bar_index: 0,
            show: true,
          },
        ],
        rhythmBars: [
          {
            band: "kick",
            bar_index: 0,
            t_start: 0,
            t_end: 2,
            pattern: "quarter",
            stable: true,
          },
        ],
      },
      "kick",
    );
    expect(lines.some((line) => line.startsWith("quarter"))).toBe(true);
    expect(lines).toContain("quarter  steady");
  });

  it("shows the pitch cuts once the pitch row is open", () => {
    const data: InspectMusicResponse = {
      kind: "inspect_music_response",
      duration_seconds: 10,
      pitch_backend: "basic_pitch",
      pitch: [{ t_seconds: 1, midi: 60, confidence: 0.4 }],
      notes: [{ t_start: 0.8, t_end: 1.2, midi: 60, confidence: 0.6 }],
    };
    class Observer {
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", Observer);
    render(
      <MusicLayers
        data={data}
        view={{ start: 0, end: 10, span: 10 }}
        playhead={1}
        status="ready"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "pitch" }));
    const contour = screen.getByRole("slider", { name: "contour" });
    expect(contour).toHaveValue("0.2");
    expect(screen.getByRole("checkbox", { name: "fold octaves" })).toBeChecked();
    fireEvent.change(contour, { target: { value: "0.5" } });
    expect(contour).toHaveValue("0.5");
  });

  it("folds every octave onto one C-to-C line and eases a short jump", () => {
    expect(foldMidi(60)).toBe(0);
    expect(foldMidi(72)).toBe(0);
    expect(foldMidi(67)).toBe(7);
    const pitch = [
      { t_seconds: 0, midi: 60, confidence: 0.9 },
      { t_seconds: 0.05, midi: 72, confidence: 0.9 },
      { t_seconds: 0.1, midi: 67, confidence: 0.9 },
      { t_seconds: 0.15, midi: 60, confidence: 0.9 },
    ];
    const folded = displayMelody(pitch, [], { ...DEFAULT_MELODY, correct: false });
    expect(folded.pitch.map((p) => p.midi)).toEqual([0, 0, 7, 0]);
    expect(folded.pitch[1]?.heard).toBe(72);
    const eased = displayMelody(pitch, [], { ...DEFAULT_MELODY, strength: 1, window: 0.2 });
    expect(eased.pitch[2]?.midi).toBeLessThan(3);
    const kept = displayMelody(pitch, [], { ...DEFAULT_MELODY, fold: false });
    expect(kept.pitch.map((p) => p.midi)).toEqual([60, 72, 67, 60]);
  });

  it("shifts the scale by the band's median cents and drops a short nearby blip", () => {
    const pitch = Array.from({ length: 24 }, (_, i) => ({
      t_seconds: i * 0.023,
      midi: 60.2,
      confidence: 0.8,
    }));
    pitch[12] = { t_seconds: 12 * 0.023, midi: 61.2, confidence: 0.8 };
    const out = displayMelody(pitch, [], {
      ...DEFAULT_MELODY,
      fold: false,
      correct: false,
      blip: 0.08,
    });
    expect(out.tuningCents).toBe(20);
    expect(out.offset).toBeCloseTo(0.2, 5);
    expect(out.pitch[12]?.midi).toBeCloseTo(60.2, 5);
    expect(out.pitch[11]?.midi).toBeCloseTo(60.2, 5);
  });

  it("moves a note start onto a vocal onset that already sits on a beat", () => {
    const pitch = [
      { t_seconds: 0, midi: 60, confidence: 0.9 },
      { t_seconds: 0.5, midi: 60, confidence: 0.9 },
      { t_seconds: 1.03, midi: 62, confidence: 0.9 },
      { t_seconds: 1.2, midi: 62, confidence: 0.9 },
    ];
    const onBeat = displayMelody(
      pitch,
      [],
      { ...DEFAULT_MELODY, fold: false, alignSec: 0.04 },
      { beats: [1], vocalOnsets: [1] },
    );
    expect(onBeat.pitch[2]?.t_seconds).toBeCloseTo(1, 2);
    expect(onBeat.pitch[3]?.t_seconds).toBeCloseTo(1.2, 2);
    const offBeat = displayMelody(
      pitch,
      [],
      { ...DEFAULT_MELODY, fold: false, alignSec: 0.04 },
      { beats: [1.5], vocalOnsets: [1] },
    );
    expect(offBeat.pitch[2]?.t_seconds).toBeCloseTo(1.03, 2);
  });

  it("drops pitches outside a congregational singing range", () => {
    const pitch = [
      { t_seconds: 0, midi: 36, confidence: 0.9 },
      { t_seconds: 0.1, midi: 60, confidence: 0.9 },
      { t_seconds: 0.2, midi: 90, confidence: 0.9 },
    ];
    const notes = [
      { t_start: 0, t_end: 0.3, midi: 29, confidence: 0.8 },
      { t_start: 0.4, t_end: 0.8, midi: 64, confidence: 0.8 },
    ];
    const kept = displayMelody(pitch, notes, {
      ...DEFAULT_MELODY,
      fold: false,
      snap: false,
      tune: false,
    });
    expect(kept.pitch.map((p) => p.heard)).toEqual([60]);
    expect(kept.notes.map((n) => n.heard)).toEqual([64]);
    const all = displayMelody(pitch, notes, {
      ...DEFAULT_MELODY,
      fold: false,
      snap: false,
      tune: false,
      singingRange: false,
    });
    expect(all.pitch).toHaveLength(3);
    expect(all.notes).toHaveLength(2);
  });
});
