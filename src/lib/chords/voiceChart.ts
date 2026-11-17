/**
 * Spoken chord editing. A short microphone clip is transcribed and handed to
 * the chord model, which places the progression on the open song. Numbers
 * such as `1` `4` `5` `6m` are resolved in the song's key here.
 *
 * Slide edits ("verse 3 is actually a chorus") are only asked for when the
 * caller passes `slideEdits` — the shipped editor charts only.
 */

import { alignSlides, chartHasChords, emptyChart, type ChordChart } from "@/lib/chords/chart";
import { resolveChordInput, setWordChordInBody, toChordText } from "@/lib/chords/editorText";
import { buildChordLines, findLineWord, parseLineId } from "@/lib/chords/lineWords";
import { parseSectionTag, parseSong, type ParsedSection, type SectionType } from "@/lib/parseSong";
import { slidesToBody, type OverrideSlide } from "@/lib/slideSequence";
import { requestSidecar } from "@/lib/sidecarClient";
import type {
  ChordPlacementInfo,
  VoiceChartClear,
  VoiceChartHold,
  VoiceSlideEdit,
} from "@/types/generated";

const MAX_RECORDING_MS = 20_000;
/** A whole song: an instruction may name any section. */
const MAX_VOICE_LINES = 160;
export const VOICE_SILENCE_MS = 2_000;
export const VOICE_MIN_SPEECH_MS = 400;

/** Slides whose words, section, or chords differ after a voice take. */
export function changedSlideIndexes(
  beforeBody: string,
  afterBody: string,
  beforeChart: ChordChart | null,
  afterChart: ChordChart | null,
): number[] {
  const before = parseSong(beforeBody).slides;
  const after = parseSong(afterBody).slides;
  const beforeChords = chordsBySlide(beforeChart, before.map((slide) => slide.lyrics));
  const afterChords = chordsBySlide(afterChart, after.map((slide) => slide.lyrics));
  const indexes: number[] = [];
  const count = Math.max(before.length, after.length);
  for (let i = 0; i < count; i++) {
    const left = before[i];
    const right = after[i];
    if (!right) continue;
    if (!left) {
      indexes.push(i);
      continue;
    }
    const sectionChanged =
      left.section.type !== right.section.type || left.section.number !== right.section.number;
    const lyricsChanged = left.lyrics !== right.lyrics;
    const chordsChanged = beforeChords[i] !== afterChords[i];
    if (sectionChanged || lyricsChanged || chordsChanged) indexes.push(i);
  }
  return indexes;
}

function chordsBySlide(chart: ChordChart | null, lyrics: string[]): string[] {
  if (!chart || !chartHasChords(chart)) return lyrics.map(() => "");
  const aligned = alignSlides(chart, [{ lyrics: "" }, ...lyrics.map((line) => ({ lyrics: line }))]).slice(1);
  return lyrics.map((_, index) =>
    (aligned[index]?.lines ?? [])
      .map((line) => line.chords.map((chord) => `${chord.pos}:${chord.chord}`).join(","))
      .join("|"),
  );
}

/** A two-second pause after real speech is the moment to send the utterance. */
export function utteranceBoundary(state: {
  speechMs: number;
  silenceMs: number;
  holding: boolean;
  busy: boolean;
}): "flush" | "wait" {
  if (state.busy || state.holding) return "wait";
  if (state.speechMs >= VOICE_MIN_SPEECH_MS && state.silenceMs >= VOICE_SILENCE_MS) return "flush";
  return "wait";
}

export function applyVoiceChart(
  body: string,
  chart: ChordChart | null,
  placements: ChordPlacementInfo[],
  holds: VoiceChartHold[],
  songKey: string | null | undefined,
  clears: VoiceChartClear[] = [],
): { chordText: string; chart: ChordChart | null } {
  let state = { chordText: body, chart: chart ?? (songKey ? { ...emptyChart(), key: songKey } : null) };

  for (const clear of clears) {
    if (clear.word) continue;
    const found = findLine(body, clear.line_id);
    if (!found || !state.chart) continue;
    const line = found.slideLyrics.split("\n")[found.lineIdx] ?? "";
    state = setWordChordInBody(
      body,
      state.chart,
      found.slideLyrics,
      found.lineIdx,
      0,
      line.length + 32,
      0,
      null,
    );
  }

  for (const clear of clears) {
    if (!clear.word) continue;
    const found = findWord(body, clear.line_id, clear.word, clear.word_index, new Set());
    if (!found || !state.chart) continue;
    state = setWordChordInBody(
      body,
      state.chart,
      found.slideLyrics,
      found.lineIdx,
      found.wordStart,
      found.wordEnd,
      found.chordPos,
      null,
    );
  }

  for (const group of groupPlacements(body, placements)) {
    const chord = group.chords
      .map((symbol) => resolveChordInput(symbol, songKey))
      .filter(Boolean)
      .join(" ");
    if (!chord) continue;
    state = setWordChordInBody(
      body,
      state.chart,
      group.slideLyrics,
      group.lineIdx,
      group.wordStart,
      group.wordEnd,
      group.chordPos,
      chord,
    );
  }

  for (const hold of holds) {
    const found = findWord(body, hold.line_id, "", undefined, new Set());
    if (!found) continue;
    const chord = resolveChordInput(hold.chord, songKey);
    if (!chord) continue;
    const bars = Math.max(1, Math.min(hold.bars ?? 1, 8));
    const line = found.slideLyrics.split("\n")[found.lineIdx] ?? "";
    const pos = line.length + 1;
    state = setWordChordInBody(
      body,
      state.chart,
      found.slideLyrics,
      found.lineIdx,
      pos,
      pos + 1,
      pos,
      Array.from({ length: bars }, () => chord).join(" "),
    );
  }

  return state;
}

export interface VoiceChartTurn {
  transcript: string;
  applied: string;
}

/** One line the next take can read: which word got which chord, and any hold. */
export function describeVoiceChart(
  placements: ChordPlacementInfo[],
  holds: VoiceChartHold[],
  clears: VoiceChartClear[] = [],
): string {
  const at = (index: number | undefined) => (index !== undefined && index >= 0 ? `[${index}]` : "");
  const placed = placements.map((item) => `${item.line_id} ${item.word}${at(item.word_index)}=${item.chord}`);
  const held = holds.map((item) => `hold ${item.line_id} ${item.chord} x${item.bars ?? 1}`);
  const cleared = clears.map((item) =>
    item.word ? `clear ${item.line_id} ${item.word}${at(item.word_index)}` : `clear ${item.line_id}`,
  );
  return [...placed, ...held, ...cleared].join("; ");
}

export function applySlideEdits(
  body: string,
  edits: VoiceSlideEdit[],
): { body: string; notes: string[] } {
  let slides: OverrideSlide[] = parseSong(body).slides.map((slide) => ({
    section: { ...slide.section },
    lyrics: slide.lyrics,
  }));
  const notes: string[] = [];
  for (const edit of edits) {
    const next = applyOneSlideEdit(slides, edit);
    if (!next) continue;
    slides = next.slides;
    notes.push(next.note);
  }
  if (notes.length === 0) return { body, notes };
  return { body: slidesToBody(slides), notes };
}

function applyOneSlideEdit(
  slides: OverrideSlide[],
  edit: VoiceSlideEdit,
): { slides: OverrideSlide[]; note: string } | null {
  const indexes = slideTargets(slides, edit);
  if (indexes.length === 0) return null;
  const op = edit.op;
  if (op === "rename") {
    const section = renamedSection(edit);
    if (!section) return null;
    const next = slides.map((slide, index) =>
      indexes.includes(index) ? { ...slide, section } : slide,
    );
    return { slides: next, note: `rename ${edit.section || indexes.join(",")} to ${section.type} ${section.number}` };
  }
  if (op === "split") {
    const index = indexes[0]!;
    const lines = slides[index]!.lyrics.split("\n");
    const after = edit.after_line ?? -1;
    if (after < 0 || after >= lines.length - 1) return null;
    const left = { ...slides[index]!, lyrics: lines.slice(0, after + 1).join("\n") };
    const right = { ...slides[index]!, section: { ...slides[index]!.section }, lyrics: lines.slice(after + 1).join("\n") };
    return {
      slides: [...slides.slice(0, index), left, right, ...slides.slice(index + 1)],
      note: `split slide ${index} after line ${after}`,
    };
  }
  if (op === "merge") {
    const sorted = [...indexes].sort((a, b) => a - b);
    const first = sorted[0]!;
    const run = consecutive(sorted, first);
    const block = run.length > 1 ? run : [first, first + 1].filter((index) => slides[index]);
    if (block.length < 2) return null;
    const lyrics = block.map((index) => slides[index]!.lyrics).join("\n");
    const merged = { ...slides[first]!, lyrics };
    return {
      slides: [...slides.slice(0, first), merged, ...slides.slice(block[block.length - 1]! + 1)],
      note: `merge slides ${block.join(",")}`,
    };
  }
  if (op === "delete") {
    const drop = new Set(indexes);
    return {
      slides: slides.filter((_, index) => !drop.has(index)),
      note: `delete ${edit.section || indexes.join(",")}`,
    };
  }
  if (op === "duplicate") {
    const copies = indexes.map((index) => ({
      section: { ...slides[index]!.section },
      lyrics: slides[index]!.lyrics,
    }));
    const after = Math.max(...indexes);
    return {
      slides: [...slides.slice(0, after + 1), ...copies, ...slides.slice(after + 1)],
      note: `duplicate ${edit.section || indexes.join(",")}`,
    };
  }
  if (op === "move") {
    const anchor = slides
      .map((slide, index) => ({ slide, index }))
      .filter((item) => !indexes.includes(item.index) && sectionMatches(item.slide.section, edit.anchor ?? ""));
    if (anchor.length === 0) return null;
    const moving = indexes.map((index) => slides[index]!);
    const rest = slides.filter((_, index) => !indexes.includes(index));
    const anchorIndex = edit.place === "after" ? anchor[anchor.length - 1]!.index : anchor[0]!.index;
    const insertAt = rest.findIndex((slide) => slide === slides[anchorIndex]);
    if (insertAt < 0) return null;
    const at = edit.place === "after" ? insertAt + 1 : insertAt;
    return {
      slides: [...rest.slice(0, at), ...moving, ...rest.slice(at)],
      note: `move ${edit.section || indexes.join(",")} ${edit.place ?? "before"} ${edit.anchor}`,
    };
  }
  if (op === "lyrics") {
    const text = (edit.lyrics ?? "").trim();
    if (!text) return null;
    const index = indexes[0]!;
    const next = slides.map((slide, i) => (i === index ? { ...slide, lyrics: text } : slide));
    return { slides: next, note: `lyrics slide ${index}` };
  }
  return null;
}

function slideTargets(slides: OverrideSlide[], edit: VoiceSlideEdit): number[] {
  if ((edit.slide ?? -1) >= 0) {
    const index = edit.slide ?? -1;
    return index < slides.length ? [index] : [];
  }
  return slides
    .map((slide, index) => (sectionMatches(slide.section, edit.section ?? "") ? index : -1))
    .filter((index) => index >= 0);
}

function sectionMatches(section: ParsedSection, name: string): boolean {
  const text = name.trim().toLowerCase();
  if (!text) return false;
  const parsed = parseSectionTag(text);
  if (/\d/.test(text)) return section.type === parsed.type && section.number === parsed.number;
  return section.type === parsed.type;
}

function renamedSection(edit: VoiceSlideEdit): ParsedSection | null {
  const type = (edit.section_type ?? "").trim().toLowerCase().replace(/\s+/g, "-");
  const parsed = parseSectionTag(type === "prechorus" ? "pre-chorus" : type || "other");
  if (!edit.section_type?.trim()) return null;
  const known: SectionType[] = ["verse", "chorus", "bridge", "tag", "intro", "outro", "pre-chorus", "instrumental", "other"];
  if (!known.includes(parsed.type) || (parsed.type === "other" && type !== "other")) return null;
  const number = edit.section_number && edit.section_number > 0 ? edit.section_number : parsed.number || 1;
  return { type: parsed.type, number };
}

function consecutive(indexes: number[], first: number): number[] {
  const run = [first];
  for (const index of indexes.slice(1)) {
    if (index !== run[run.length - 1]! + 1) break;
    run.push(index);
  }
  return run;
}

export async function requestVoiceChart(
  body: string,
  chart: ChordChart | null,
  songKey: string | null | undefined,
  audio: Blob,
  history: VoiceChartTurn[] = [],
  final = false,
  slideEdits = false,
): Promise<
  | { transcript: string; body: string; chordText: string; chart: ChordChart | null; applied: string }
  | { error: string; transcript: string }
  | { incomplete: true; transcript: string }
> {
  const lines = buildChordLines(body, chart, { maxLines: MAX_VOICE_LINES });
  const reply = await requestSidecar(
    {
      kind: "voice_chart",
      song_key: songKey ?? "",
      lines,
      audio_base64: await blobToBase64(audio),
      audio_mime: audio.type || "audio/webm",
      instruction: "",
      history: history.slice(-8).map((turn) => ({
        transcript: turn.transcript,
        applied: turn.applied,
      })),
      final,
      slide_edits: slideEdits,
    },
    { timeoutMs: 45_000 },
  );
  if (reply.complete === false) return { incomplete: true, transcript: reply.transcript ?? "" };
  if (reply.error) return { error: reply.error, transcript: reply.transcript ?? "" };
  const placements = reply.placements ?? [];
  const holds = reply.holds ?? [];
  const clears = reply.clears ?? [];
  const edits = reply.slide_edits ?? [];
  if (placements.length === 0 && holds.length === 0 && clears.length === 0 && edits.length === 0) {
    return {
      error: reply.transcript
        ? `Heard “${reply.transcript}”, but it didn't land on these lyrics.`
        : "Didn't catch a chord progression in that.",
      transcript: reply.transcript ?? "",
    };
  }
  const edited = slideEdits ? applySlideEdits(body, edits) : { body, notes: [] as string[] };
  const chorded = applyVoiceChart(edited.body, chart, placements, holds, songKey, clears);
  const chordText =
    placements.length > 0 || holds.length > 0 || clears.length > 0
      ? chorded.chordText
      : toChordText(edited.body, chorded.chart);
  const notes = [...edited.notes];
  const chordNote = describeVoiceChart(placements, holds, clears);
  if (chordNote) notes.push(chordNote);
  return {
    transcript: reply.transcript ?? "",
    body: edited.body,
    applied: notes.join("; "),
    chordText,
    chart: chorded.chart,
  };
}

let warmStream: MediaStream | null = null;
let warmPromise: Promise<MediaStream> | null = null;

/** Open the microphone before the take, and keep it open between takes.

The click used to wait on `getUserMedia` and then close the mic, so the
start of the next sentence was spoken before the recorder was running.
*/
export function warmVoiceInput(): void {
  if (warmStream || warmPromise) return;
  const open = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  if (!open) return;
  warmPromise = open({ audio: true }).then(
    (stream) => {
      warmStream = stream;
      return stream;
    },
    (err: unknown) => {
      warmPromise = null;
      throw err;
    },
  );
  void warmPromise.catch(() => {});
}

export function releaseVoiceInput(): void {
  warmStream?.getTracks().forEach((track) => track.stop());
  warmStream = null;
  warmPromise = null;
}

function recordingMime(): string | undefined {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return undefined;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
    MediaRecorder.isTypeSupported(type),
  );
}

type OpenClip = { recorder: MediaRecorder; chunks: Blob[]; stopped: Promise<void> };

function openClip(stream: MediaStream, mime: string | undefined): OpenClip {
  const chunks: Blob[] = [];
  const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  let markStopped = () => {};
  const stopped = new Promise<void>((resolve) => {
    markStopped = resolve;
  });
  recorder.addEventListener("stop", () => markStopped(), { once: true });
  // A timeslice flushes the first frames. Without it, an MP4 recording can
  // drop the opening words before Whisper ever sees them.
  recorder.start(100);
  return { recorder, chunks, stopped };
}

async function closeClip(clip: OpenClip, mime: string | undefined): Promise<Blob> {
  if (clip.recorder.state === "recording") {
    clip.recorder.requestData();
    clip.recorder.stop();
    await clip.stopped;
  }
  return new Blob(clip.chunks, { type: clip.recorder.mimeType || mime || "audio/webm" });
}

/** Record until `stop`. A two-second pause sends a finished sentence on its own.

Each sentence is its own recorder. A later slice of one long WebM has no
file header, and Whisper rejects it.
*/
export async function startVoiceRecording(handlers: {
  onUtterance: (blob: Blob, final: boolean) => Promise<"applied" | "incomplete" | "error">;
}): Promise<{ stop: () => Promise<void> }> {
  const stream = warmStream ?? (await (warmPromise ?? navigator.mediaDevices.getUserMedia({ audio: true })));
  warmStream = stream;
  warmPromise = null;
  const mime = recordingMime();
  const audio = new AudioContext();
  void audio.resume();
  const analyser = audio.createAnalyser();
  analyser.fftSize = 512;
  audio.createMediaStreamSource(stream).connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);

  let clip = openClip(stream, mime);
  let carried: Blob | null = null;
  let speechMs = 0;
  let silenceMs = 0;
  let holding = false;
  let busy = false;
  const startedAt = Date.now();

  async function flush(final: boolean): Promise<void> {
    if (busy) return;
    if (clip.chunks.length === 0 && !carried) return;
    busy = true;
    holding = true;
    try {
      let blob: Blob | null = null;
      if (clip.chunks.length > 0) {
        const closed = clip;
        // Stop before starting the next one. Two recorders on one mic stream
        // is what dropped the header off every sentence after the first.
        blob = await closeClip(closed, mime);
        clip = openClip(stream, mime);
        speechMs = 0;
        silenceMs = 0;
      }
      if (carried && blob) blob = await joinClips(audio, [carried, blob]);
      else blob = blob ?? carried;
      if (!blob || blob.size === 0) return;
      const status = await handlers.onUtterance(blob, final);
      if (status === "incomplete" && !final) {
        carried = blob;
        return;
      }
      carried = null;
      holding = false;
    } finally {
      busy = false;
    }
  }

  const timer = window.setInterval(() => {
    if (stopPromise) return;
    if (Date.now() - startedAt >= MAX_RECORDING_MS * 9) {
      void stop();
      return;
    }
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const value of samples) {
      const centered = (value - 128) / 128;
      sum += centered * centered;
    }
    const loud = Math.sqrt(sum / samples.length) >= 0.02;
    if (loud) {
      speechMs += 100;
      silenceMs = 0;
      holding = false;
    } else if (speechMs > 0) {
      silenceMs += 100;
    }
    if (utteranceBoundary({ speechMs, silenceMs, holding, busy }) === "flush") void flush(false);
  }, 100);

  let stopPromise: Promise<void> | null = null;
  function stop(): Promise<void> {
    if (stopPromise) return stopPromise;
    stopPromise = (async () => {
      window.clearInterval(timer);
      while (busy) await new Promise((resolve) => window.setTimeout(resolve, 30));
      if (speechMs >= VOICE_MIN_SPEECH_MS || carried) await flush(true);
      if (clip.recorder.state === "recording") clip.recorder.stop();
      await audio.close().catch(() => {});
    })();
    return stopPromise;
  }

  return { stop };
}

async function joinClips(audio: AudioContext, parts: Blob[]): Promise<Blob> {
  const buffers: AudioBuffer[] = [];
  for (const part of parts) {
    try {
      buffers.push(await audio.decodeAudioData(await part.arrayBuffer()));
    } catch {
      // A clip the browser can't decode is dropped rather than failing the take.
    }
  }
  if (buffers.length === 0) return parts[parts.length - 1]!;
  if (buffers.length === 1) return parts.length === 1 ? parts[0]! : encodeWav(buffers[0]!);
  const rate = buffers[0]!.sampleRate;
  const length = buffers.reduce((total, buffer) => total + buffer.length, 0);
  const offline = new OfflineAudioContext(1, length, rate);
  let offset = 0;
  for (const buffer of buffers) {
    const source = offline.createBufferSource();
    source.buffer = buffer;
    source.connect(offline.destination);
    source.start(offset / rate);
    offset += buffer.length;
  }
  return encodeWav(await offline.startRendering());
}

function encodeWav(buffer: AudioBuffer): Blob {
  const channel = buffer.getChannelData(0);
  const pcm = new Int16Array(channel.length);
  for (let i = 0; i < channel.length; i++) {
    const sample = Math.max(-1, Math.min(1, channel[i]!));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  const wav = new ArrayBuffer(44 + pcm.byteLength);
  const view = new DataView(wav);
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + pcm.byteLength, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, pcm.byteLength, true);
  new Int16Array(wav, 44).set(pcm);
  return new Blob([wav], { type: "audio/wav" });
}

function groupPlacements(body: string, placements: ChordPlacementInfo[]) {
  const groups: {
    slideLyrics: string;
    lineIdx: number;
    wordStart: number;
    wordEnd: number;
    chordPos: number;
    chords: string[];
  }[] = [];
  const used = new Map<string, Set<number>>();
  for (const placement of placements) {
    const taken = used.get(placement.line_id) ?? new Set<number>();
    const found = findWord(body, placement.line_id, placement.word, placement.word_index, taken);
    if (!found || !placement.chord.trim()) continue;
    taken.add(found.wordIndex);
    used.set(placement.line_id, taken);
    const existing = groups.find(
      (group) =>
        group.slideLyrics === found.slideLyrics &&
        group.lineIdx === found.lineIdx &&
        group.wordStart === found.wordStart,
    );
    if (existing) {
      existing.chords.push(placement.chord.trim());
    } else {
      groups.push({ ...found, chords: [placement.chord.trim()] });
    }
  }
  return groups;
}

function findLine(
  body: string,
  lineId: string,
): { slideLyrics: string; lineIdx: number } | null {
  const at = parseLineId(lineId);
  if (!at) return null;
  const slide = parseSong(body).slides[at.slideIdx];
  const line = slide?.lyrics.split("\n")[at.lineIdx];
  if (!slide || line === undefined) return null;
  return { slideLyrics: slide.lyrics, lineIdx: at.lineIdx };
}

/** The word a placement names; a blank word means just after the line (a hold). */
function findWord(
  body: string,
  lineId: string,
  word: string | undefined,
  index: number | undefined,
  avoid: ReadonlySet<number>,
): { slideLyrics: string; lineIdx: number; wordIndex: number; wordStart: number; wordEnd: number; chordPos: number } | null {
  const found = findLine(body, lineId);
  if (!found) return null;
  const line = found.slideLyrics.split("\n")[found.lineIdx] ?? "";
  if (!word && (index === undefined || index < 0)) {
    return { ...found, wordIndex: -1, wordStart: line.length + 1, wordEnd: line.length + 2, chordPos: line.length + 1 };
  }
  const hit = findLineWord(line, word ?? "", index, avoid);
  if (!hit) return null;
  return { ...found, wordIndex: hit.index, wordStart: hit.start, wordEnd: hit.end, chordPos: hit.start };
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const data = String(reader.result ?? "");
      resolve(data.slice(data.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Couldn't read the recording"));
    reader.readAsDataURL(blob);
  });
}
