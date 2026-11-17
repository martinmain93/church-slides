import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const glide = vi.hoisted(() => vi.fn((..._a: unknown[]) => () => {}));
vi.mock("@/lib/band/glide", () => ({ glideScrollLeft: glide }));

import { BandView, keyChoices } from "@/components/band/BandView";
import { normalizeTitle } from "@/lib/chords/import/plan";
import { isBandCommand, type BandPosition, type BandSetlist } from "@/lib/band/protocol";
import { bandInviteUrl, bandSocketUrl, useBandConnection } from "@/lib/band/useBandConnection";
import { buildBandSetlist, resolveBandCommand } from "@/lib/band/useBandHost";
import { importChartFile } from "@/lib/chords/import";
import { chartToSong } from "@/lib/chords/import/toSong";
import { buildContext } from "@/lib/presentation/state";
import type { SetlistDetail, SongRow } from "@/lib/tauri";

function song(id: string, title: string, body: string, slide_order: string | null = null): SongRow {
  return { id, title, body_text: body, ccli: null, author: null, copyright: null, default_tempo_wps: null, slide_order, created_at: 0, updated_at: 0 };
}

const [imported] = importChartFile(
  "grace.cho",
  "{title: Amazing Grace}\n{key: G}\n{sov: Verse 1}\nA[G]mazing grace how [C]sweet the sound\n{eov}\n{soc}\n[D]My chains are gone\n{eoc}",
).songs;
const graceBody = chartToSong(imported!);

const detail: SetlistDetail = {
  id: "sl1",
  name: "Sunday",
  created_at: 0,
  last_used_at: null,
  items: [
    { id: "it1", setlist_id: "sl1", kind: "song", order: 0, song_id: "s1", slides_override: null, song: song("s1", "Amazing Grace", graceBody.body_text, graceBody.slide_order) },
    { id: "it2", setlist_id: "sl1", kind: "song", order: 1, song_id: "s2", slides_override: null, song: song("s2", "Second Song", "[Verse 1]\nHello") },
  ],
} as unknown as SetlistDetail;

describe("band host", () => {
  const ctx = buildContext(detail);

  it("publishes each song's projector slides, chart and transpose", () => {
    const snap = buildBandSetlist(ctx, { s1: imported!.chart }, { it1: 2 });
    expect(snap.songs.map((s) => s.title)).toEqual(["Amazing Grace", "Second Song"]);
    expect(snap.songs[0]!.slides[0]!.lyrics).toBe(""); // leading blank
    expect(snap.songs[0]!.chart?.key).toBe("G");
    expect(snap.songs[0]!.transpose).toBe(2);
    expect(snap.songs[1]!.chart).toBeNull();
  });

  it("turns a leader's song pick into a jump to that song's blank slide", () => {
    expect(resolveBandCommand({ kind: "go_to_song", itemId: "it2" }, ctx)).toEqual({ type: "jump", itemIdx: 1, slideIdx: 0 });
    expect(resolveBandCommand({ kind: "go_to_song", itemId: "gone" }, ctx)).toBeNull();
    expect(resolveBandCommand({ kind: "add_song", songId: "s9", when: "now" }, ctx)).toEqual({ type: "add", songId: "s9", goNow: true, key: null });
    expect(resolveBandCommand({ kind: "set_transpose", itemId: "it1", semitones: 11 }, ctx)).toEqual({
      type: "transpose", itemId: "it1", semitones: -1,
    });
  });
});

describe("leader slide and blank commands", () => {
  const ctx = buildContext(detail);

  it("jumps to the tapped slide, and ignores one that no longer exists", () => {
    expect(resolveBandCommand({ kind: "go_to_slide", itemId: "it1", slideIdx: 2 }, ctx)).toEqual({
      type: "jump", itemIdx: 0, slideIdx: 2,
    });
    expect(resolveBandCommand({ kind: "go_to_slide", itemId: "it1", slideIdx: 99 }, ctx)).toBeNull();
    expect(resolveBandCommand({ kind: "go_to_slide", itemId: "gone", slideIdx: 0 }, ctx)).toBeNull();
  });

  it("passes blank through", () => {
    expect(resolveBandCommand({ kind: "set_blank", on: true }, ctx)).toEqual({ type: "blank", on: true });
  });

  it("rejects malformed commands", () => {
    expect(isBandCommand({ kind: "go_to_slide", itemId: "it1", slideIdx: -1 })).toBe(false);
    expect(isBandCommand({ kind: "go_to_slide", itemId: "it1", slideIdx: 1.5 })).toBe(false);
    expect(isBandCommand({ kind: "set_blank", on: "yes" })).toBe(false);
    expect(isBandCommand({ kind: "set_blank", on: false })).toBe(true);
  });
});

describe("band socket address", () => {
  it("uses the same origin, or the band port when served by the dev server", () => {
    expect(bandSocketUrl({ protocol: "http:", host: "192.168.1.5:7777", hostname: "192.168.1.5", search: "" } as Location)).toBe("ws://192.168.1.5:7777/ws");
    expect(bandSocketUrl({ protocol: "http:", host: "localhost:1420", hostname: "localhost", search: "?window=band&bandPort=7777" } as Location)).toBe("ws://localhost:7777/ws");
  });
});

describe("import title matching", () => {
  it("ignores case, punctuation and parentheticals", () => {
    expect(normalizeTitle("10,000 Reasons (Bless The Lord)")).toBe(normalizeTitle("10000 reasons"));
    expect(normalizeTitle("What A Beautiful Name")).toBe(normalizeTitle("What a beautiful name!"));
  });
});

// --- the band screen, over a fake socket ---------------------------------------

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  deliver(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

describe("band invite link", () => {
  it("is the band link on this server or relay room, never the leader's PIN link", () => {
    const at = (origin: string, pathname: string, search = "") => ({ origin, pathname, search }) as Location;
    expect(bandInviteUrl(at("http://192.168.1.5:7777", "/leader", "?pin=1234"))).toBe("http://192.168.1.5:7777/band");
    expect(bandInviteUrl(at("https://relay.example", "/r/abc123/leader", "?k=K9&pin=1234"))).toBe("https://relay.example/r/abc123/band?k=K9");
    expect(bandInviteUrl(at("https://relay.example", "/r/abc123/band", "?k=K9"))).toBe("https://relay.example/r/abc123/band?k=K9");
  });
});

describe("band screen", () => {
  const setlist: BandSetlist = buildBandSetlist(buildContext(detail), { s1: imported!.chart }, {});

  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
    Element.prototype.scrollIntoView = vi.fn();
    window.history.replaceState(null, "", "/band");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function connect() {
    const sock = FakeSocket.instances.at(-1)!;
    act(() => sock.open());
    return sock;
  }

  it("follows the operator with chords over lyrics", () => {
    render(<BandView />);
    const sock = connect();
    expect(JSON.parse(sock.sent[0]!)).toEqual({ type: "hello", role: "band" });
    const position: BandPosition = { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" };
    act(() => {
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: position });
    });
    expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
    expect(screen.getByText("Next: Second Song")).toBeInTheDocument();
    expect(screen.getByTestId("band-key")).toHaveTextContent("G");
    const active = document.querySelector("[data-active]")!;
    // Chords and lyrics live in separate rows of each word now; read each.
    const chordsOf = (el: Element) =>
      [...el.querySelectorAll("[data-testid=chord-line] .text-sky-400")].map((c) => c.textContent);
    const lyricsOf = (el: Element) =>
      [...el.querySelectorAll("[data-testid=chord-line] > span > span > span:last-child")].map((c) => c.textContent).join("");
    expect(lyricsOf(active)).toContain("Amazing grace how");
    expect(chordsOf(active)).toEqual(["G", "C"]);
  });

  it("Invite shows a QR of the band link for the next musician", () => {
    render(<BandView />);
    connect();
    fireEvent.click(screen.getByLabelText("Display settings"));
    fireEvent.click(screen.getByTestId("band-invite"));
    const panel = screen.getByTestId("band-invite-panel");
    expect(panel).toHaveTextContent(`${window.location.origin}/band`);
    expect(screen.getByRole("img", { name: /QR code/ })).toBeInTheDocument();
  });

  it("shows the band's key when the leader transposes", () => {
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "setlist", data: { ...setlist, songs: setlist.songs.map((s, i) => (i === 0 ? { ...s, transpose: 3 } : s)) } });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    expect(screen.getByTestId("band-key")).toHaveTextContent("Bb");
    expect([...document.querySelectorAll("[data-active] [data-testid=chord-line] .text-sky-400")].map((c) => c.textContent)).toEqual([
      "Bb",
      "Eb",
    ]);
  });

  it("reconnects when the connection drops", () => {
    vi.useFakeTimers();
    render(<BandView />);
    const sock = connect();
    act(() => sock.close());
    expect(screen.getByText(/Reconnecting/)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances.length).toBe(2);
    vi.useRealTimers();
  });

  it("treats a silent connection as dead", () => {
    vi.useFakeTimers();
    render(<BandView />);
    connect();
    act(() => vi.advanceTimersByTime(13_000));
    expect(FakeSocket.instances.length).toBeGreaterThan(1);
    vi.useRealTimers();
  });

  it("leader: All songs lists the set; Full library searches and adds a song", () => {
    window.history.replaceState(null, "", "/leader?pin=1234");
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "welcome", role: "leader" });
      sock.deliver({ type: "setlist", data: { ...setlist, canAddSongs: true } });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 0, itemId: "it1", blankMode: "none" } });
    });
    // The leader fetches the library as soon as it's unlocked.
    expect(sock.sent.map((m) => JSON.parse(m).type)).toContain("library_request");
    act(() => {
      sock.deliver({
        type: "library",
        data: [
          { id: "s1", title: "Amazing Grace", author: null, body: "Amazing grace", lastUsed: 10, uses: 3, key: "G", hasChart: true },
          { id: "s9", title: "Way Maker", author: "Sinach", body: "[Chorus]\nWay maker miracle worker", lastUsed: null, uses: 0, key: null, hasChart: false },
        ],
      });
    });

    act(() => screen.getByTestId("all-songs").click());
    const grid = screen.getByTestId("set-grid");
    expect(grid.querySelectorAll("li")).toHaveLength(2);
    expect(grid.querySelector("[aria-current]")!.textContent).toContain("Amazing Grace");

    act(() => screen.getByTestId("open-library").click());
    const input = screen.getByLabelText("Search the library");
    expect(document.activeElement).toBe(input);
    // Before typing: recently played, with the song already in the set marked.
    expect(screen.getByTestId("library-results").textContent).toContain("Amazing Grace");
    expect(screen.getByTestId("library-results").textContent).toContain("in set");

    act(() => {
      fireEvent.change(input, { target: { value: "miracle" } });
    });
    const results = screen.getByTestId("library-results");
    expect(results.textContent).toContain("Way Maker");
    expect(results.textContent).toContain("Way maker miracle worker"); // the matching lyric
    act(() => screen.getByText("Way Maker").click());
    act(() => screen.getByText("Play next").click());
    expect(JSON.parse(sock.sent.at(-1)!)).toEqual({ type: "command", command: { kind: "add_song", songId: "s9", when: "next" } });
    expect(screen.queryByLabelText("Search the library")).toBeNull();
  });

  it("leader: the key in the header drops down a key picker that moves the band", () => {
    window.history.replaceState(null, "", "/leader?pin=1234");
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "welcome", role: "leader" });
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    // No key row under the song list any more, and no all-songs icon in the header.
    expect(screen.queryByLabelText("Key up a semitone")).toBeNull();
    expect(screen.getByTestId("all-songs").closest("header")).toBeNull();

    act(() => screen.getByTestId("key-button").click());
    const choices = screen.getAllByTestId("key-choice");
    expect(choices.map((c) => c.textContent?.replace("written", ""))).toEqual(
      ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"],
    );
    expect(screen.getAllByTestId("key-choice").find((c) => c.getAttribute("aria-pressed") === "true")!.textContent).toContain("G");
    act(() => choices.find((c) => c.textContent === "Bb")!.click());
    const commands = sock.sent.map((m) => JSON.parse(m)).filter((m) => m.type === "command");
    expect(commands.at(-1)).toEqual({ type: "command", command: { kind: "set_transpose", itemId: "it1", semitones: 3 } });
    expect(screen.queryByTestId("key-menu")).toBeNull(); // closes after a pick
  });

  it("band: the key menu shows the key read-only and sets this device's capo", () => {
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    act(() => screen.getByTestId("key-button").click());
    expect(screen.getAllByTestId("key-choice").every((c) => (c as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getByText("set by the worship leader")).toBeInTheDocument();
    act(() => screen.getByLabelText("Capo 2").click());
    // G with capo 2: play F shapes; the chords on the page follow.
    expect(screen.getByTestId("key-button").textContent).toContain("capo 2: F shapes");
    expect(JSON.parse(localStorage.getItem("band_screen_prefs")!).capo).toBe(2);
    // …and the capo is gone from the Aa display settings.
    act(() => screen.getByLabelText("Display settings").click());
    expect(screen.queryByText("Capo")).toBeNull();
    localStorage.removeItem("band_screen_prefs"); // don't leave a capo for later tests
  });

  it("leader: palette sends the chosen song, after a confirm mid-song", () => {
    window.history.replaceState(null, "", "/leader?pin=1234");
    render(<BandView />);
    const sock = connect();
    expect(JSON.parse(sock.sent[0]!)).toEqual({ type: "hello", role: "leader", pin: "1234" });
    act(() => {
      sock.deliver({ type: "welcome", role: "leader" });
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    const commands = () => sock.sent.map((m) => JSON.parse(m)).filter((m) => m.type === "command");
    const second = screen.getAllByTestId("palette-song")[1]!;
    act(() => second.click());
    expect(commands()).toHaveLength(0); // mid-song: asks first
    expect(second.textContent).toContain("Tap again");
    act(() => second.click());
    expect(commands()).toEqual([{ type: "command", command: { kind: "go_to_song", itemId: "it2" } }]);
  });

  it("leader: the song strip recenters on the current song at each song change, not each slide", () => {
    glide.mockClear();
    window.history.replaceState(null, "", "/leader?pin=1234");
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "welcome", role: "leader" });
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    const strip = screen.getByTestId("palette-strip");
    const stripGlides = () => glide.mock.calls.filter(([el]) => el === strip).length;
    expect(stripGlides()).toBe(1);

    act(() => sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 2, itemId: "it1", blankMode: "none" } }));
    expect(stripGlides()).toBe(1); // a slide change leaves the strip where the leader put it

    act(() => sock.deliver({ type: "position", data: { itemIdx: 1, slideIdx: 0, itemId: "it2", blankMode: "none" } }));
    expect(stripGlides()).toBe(2);
    expect(screen.getAllByTestId("palette-song")[1]).toHaveAttribute("aria-current", "true");
  });

  it("leader: tapping a slide puts it on screen; the blank button toggles", () => {
    window.history.replaceState(null, "", "/leader?pin=1234");
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "welcome", role: "leader" });
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    const commands = () => sock.sent.map((m) => JSON.parse(m)).filter((m) => m.type === "command");
    // The slide already up isn't a button.
    expect(document.querySelector("[data-active]")!.getAttribute("role")).toBeNull();
    const sections = document.querySelectorAll("main section");
    act(() => (sections[2] as HTMLElement).click());
    expect(commands().at(-1)).toEqual({ type: "command", command: { kind: "go_to_slide", itemId: "it1", slideIdx: 2 } });

    act(() => screen.getByTestId("leader-blank").click());
    expect(commands().at(-1)).toEqual({ type: "command", command: { kind: "set_blank", on: true } });
    act(() => {
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 2, itemId: "it1", blankMode: "black" } });
    });
    expect(screen.getByTestId("leader-blank")).toHaveTextContent("Show");
    act(() => screen.getByTestId("leader-blank").click());
    expect(commands().at(-1)).toEqual({ type: "command", command: { kind: "set_blank", on: false } });
  });

  it("band (not leader): slides aren't tappable and there's no blank button", () => {
    render(<BandView />);
    const sock = connect();
    act(() => {
      sock.deliver({ type: "setlist", data: setlist });
      sock.deliver({ type: "position", data: { itemIdx: 0, slideIdx: 1, itemId: "it1", blankMode: "none" } });
    });
    expect(document.querySelector("main section[role=button]")).toBeNull();
    expect(screen.queryByTestId("leader-blank")).toBeNull();
  });
});

// --- reconnecting after a device wakes ---------------------------------------------

/** Like a browser: `close` is delivered later, not inside `close()`. */
class AsyncCloseSocket {
  static instances: AsyncCloseSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    AsyncCloseSocket.instances.push(this);
  }
  send() {}
  close() {
    if (this.readyState >= 2) return;
    this.readyState = 2;
    setTimeout(() => {
      this.readyState = 3;
      this.onclose?.();
    }, 0);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

describe("band connection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    AsyncCloseSocket.instances = [];
    vi.stubGlobal("WebSocket", AsyncCloseSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps one live socket when a tablet wakes mid-reconnect", async () => {
    // A wake fires `visibilitychange` and `online` back to back. The second
    // replaced a still-connecting socket, whose late `close` then scheduled
    // a third: two sockets left open for good, per wake.
    renderHook(() => useBandConnection("band", null));
    act(() => AsyncCloseSocket.instances[0]!.open());
    act(() => AsyncCloseSocket.instances[0]!.close()); // Wi-Fi drops
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("online"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    act(() => AsyncCloseSocket.instances.filter((s) => s.readyState === 0).forEach((s) => s.open()));
    expect(AsyncCloseSocket.instances.filter((s) => s.readyState === 1)).toHaveLength(1);
  });
});

describe("key choices", () => {
  it("spells each key as a band would and maps it to the shortest move", () => {
    expect(keyChoices("G")).toEqual([
      { key: "C", semitones: 5 }, { key: "Db", semitones: 6 }, { key: "D", semitones: -5 }, { key: "Eb", semitones: -4 },
      { key: "E", semitones: -3 }, { key: "F", semitones: -2 }, { key: "F#", semitones: -1 }, { key: "G", semitones: 0 },
      { key: "Ab", semitones: 1 }, { key: "A", semitones: 2 }, { key: "Bb", semitones: 3 }, { key: "B", semitones: 4 },
    ]);
    expect(keyChoices("Em").map((c) => c.key)).toContain("C#m");
  });
});
