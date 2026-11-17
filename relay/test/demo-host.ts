/**
 * Play the slides computer against a running relay (`pnpm relay:dev`), with
 * the app's own client and setlist builder: publishes a two-song service and
 * steps through its slides, logging any leader commands it receives.
 *
 *   (vite-node, from node_modules/.pnpm) relay/test/demo-host.ts -- [base] [hostKey]
 */
import { createHmac } from "node:crypto";

import { CloudHost } from "@/lib/band/cloudHost";
import { buildBandSetlist } from "@/lib/band/useBandHost";
import { importChartFile } from "@/lib/chords/import";
import { chartToSong } from "@/lib/chords/import/toSong";
import { buildContext } from "@/lib/presentation/state";
import type { SetlistDetail, SongRow } from "@/lib/tauri";

const [base = "http://127.0.0.1:8787", hostKey = "local-dev-host-key"] = process.argv.slice(2).filter((a) => a !== "--");
const room = `demo${Date.now().toString(36)}`;
const key = createHmac("sha256", hostKey).update(`room:${room}`).digest("hex").slice(0, 32);

const [grace] = importChartFile(
  "grace.cho",
  "{title: Amazing Grace}\n{key: G}\n{sov: Verse 1}\nA[G]mazing grace how [C]sweet the [G]sound\nThat [G]saved a wretch like [D]me\n{eov}\n{soc}\n[C]My chains are [G]gone, I've been set [D]free\n{eoc}",
).songs;
const g = chartToSong(grace!);
const row = (id: string, title: string, body: string, order: string | null): SongRow =>
  ({ id, title, body_text: body, ccli: null, author: null, copyright: null, default_tempo_wps: null, slide_order: order, created_at: 0, updated_at: 0 }) as SongRow;
const detail = {
  id: "sl", name: "Online demo", created_at: 0, last_used_at: null,
  items: [
    { id: "i1", setlist_id: "sl", kind: "song", order: 0, song_id: "s1", slides_override: null, song: row("s1", "Amazing Grace", g.body_text, g.slide_order) },
    { id: "i2", setlist_id: "sl", kind: "song", order: 1, song_id: "s2", slides_override: null, song: row("s2", "Second Song", "[Verse 1]\nHello there", null) },
  ],
} as unknown as SetlistDetail;
const ctx = buildContext(detail);

const host = new CloudHost(`${base.replace(/^http/, "ws")}/r/${room}/host?hk=${hostKey}&pin=2468`, {
  onCommand: (c) => {
    console.log("COMMAND", JSON.stringify(c));
    if (c.kind === "go_to_song") {
      const idx = ctx.items.findIndex((it) => it.itemId === c.itemId);
      host.publish("position", { itemIdx: idx, slideIdx: 0, itemId: c.itemId, blankMode: "none" });
    }
  },
  onClients: (n) => console.log("CLIENTS", n),
  onConnection: (s) => console.log("CONNECTION", s),
  libraryIndex: async () => [],
});
host.publish("setlist", buildBandSetlist(ctx, { s1: grace!.chart }, {}, true));
host.publish("position", { itemIdx: 0, slideIdx: 1, itemId: "i1", blankMode: "none" });
host.connect();
console.log(`BAND ${base}/r/${room}/band?k=${key}`);
console.log(`LEADER ${base}/r/${room}/leader?k=${key}&pin=2468`);
setTimeout(() => process.exit(0), 10 * 60 * 1000);
