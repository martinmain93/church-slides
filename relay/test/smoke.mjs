/**
 * End-to-end check of the relay against a running Worker (`pnpm relay:dev`,
 * or a deployed one): a slides computer and three devices through one room.
 *
 *   node relay/test/smoke.mjs [base-url] [host-key]
 */
import { createHmac } from "node:crypto";

const base = process.argv[2] ?? "http://127.0.0.1:8787";
const hostKey = process.argv[3] ?? "local-dev-host-key";
const ws = base.replace(/^http/, "ws");
const room = `smoke${Date.now().toString(36)}`;
const key = createHmac("sha256", hostKey).update(`room:${room}`).digest("hex").slice(0, 32);

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures++;
}

/** A socket that records every message, with a helper to wait for one. */
function open(url) {
  return new Promise((resolve, reject) => {
    const s = new WebSocket(url);
    const got = [];
    const waiters = [];
    s.onmessage = (e) => {
      const m = e.data === "pong" ? { type: "pong" } : JSON.parse(e.data);
      got.push(m);
      for (const w of [...waiters]) if (w.test(m)) { waiters.splice(waiters.indexOf(w), 1); w.resolve(m); }
    };
    s.onopen = () => resolve({
      s,
      got,
      send: (m) => s.send(typeof m === "string" ? m : JSON.stringify(m)),
      next: (test, ms = 3000) =>
        new Promise((res, rej) => {
          const found = got.find(test);
          if (found) { got.splice(got.indexOf(found), 1); return res(found); }
          const w = { test, resolve: (m) => { clearTimeout(t); got.splice(got.indexOf(m), 1); res(m); } };
          const t = setTimeout(() => { waiters.splice(waiters.indexOf(w), 1); rej(new Error(`timeout waiting (${test})`)); }, ms);
          waiters.push(w);
        }),
    });
    s.onerror = () => reject(new Error(`could not open ${url}`));
  });
}

// Pages and refusals.
const page = await fetch(`${base}/r/${room}/band?k=${key}`);
check("band page served with a valid key", page.status === 200 && (await page.text()).includes("/assets/band.js"));
const bad = await fetch(`${base}/r/${room}/band?k=nope`);
check("band page refused with a wrong key", bad.status === 403);
const js = await fetch(`${base}/assets/band.js`);
check("band script served", js.status === 200 && (await js.text()).length > 50_000);
try {
  await open(`${ws}/r/${room}/host?hk=wrong`);
  check("host refused with a wrong host key", false);
} catch {
  check("host refused with a wrong host key", true);
}

// A device that arrives before the service: told it's idle.
const early = await open(`${ws}/r/${room}/ws?k=${key}`);
check("early device told the room is idle", (await early.next((m) => m.type === "idle")).type === "idle");

const host = await open(`${ws}/r/${room}/host?hk=${hostKey}&pin=4321`);
check("host sees the device count", (await host.next((m) => m.type === "clients")).count === 1);

const setlist = { setlistId: "s", name: "Sunday", canAddSongs: true, songs: [{ itemId: "i1", title: "Big ".repeat(60_000) }] };
host.send({ type: "setlist", data: setlist });
host.send({ type: "position", data: { itemIdx: 0, slideIdx: 3, itemId: "i1", blankMode: "none" } });
const eSet = await early.next((m) => m.type === "setlist");
check("setlist relayed live, even when larger than one storage value", eSet.data.songs[0].title.length === setlist.songs[0].title.length);
check("position relayed live", (await early.next((m) => m.type === "position")).data.slideIdx === 3);

// A late device is caught up from storage.
const late = await open(`${ws}/r/${room}/ws?k=${key}`);
const lateSet = await late.next((m) => m.type === "setlist");
const latePos = await late.next((m) => m.type === "position");
check("late device caught up (setlist, then position)", lateSet.data.name === "Sunday" && latePos.data.slideIdx === 3);
check("host told of the second device", (await host.next((m) => m.type === "clients" && m.count === 2)).count === 2);

// Keep-alive: answered by the runtime.
late.send("ping");
check("ping answered with pong", (await late.next((m) => m.type === "pong")).type === "pong");

// Leader: wrong PIN, right PIN, command and library round trips.
const leader = await open(`${ws}/r/${room}/ws?k=${key}`);
leader.send({ type: "hello", role: "leader", pin: "0000" });
check("wrong PIN refused", (await leader.next((m) => m.type === "welcome")).pinRejected === true);
leader.send({ type: "command", command: { kind: "go_to_song", itemId: "i1" } });
check("command without PIN refused", (await leader.next((m) => m.type === "error")).message.includes("PIN"));
leader.send({ type: "hello", role: "leader", pin: "4321" });
check("right PIN unlocks the leader", (await leader.next((m) => m.type === "welcome")).role === "leader");
leader.send({ type: "command", command: { kind: "go_to_song", itemId: "i1" } });
check("leader command reaches the slides computer", (await host.next((m) => m.type === "command")).command.itemId === "i1");
leader.send({ type: "library_request" });
const req = await host.next((m) => m.type === "library_request");
host.send({ type: "library", to: req.from, data: [{ id: "a" }], part: 0, parts: 2 });
host.send({ type: "library", to: req.from, data: [{ id: "b" }], part: 1, parts: 2 });
const p0 = await leader.next((m) => m.type === "library" && m.part === 0);
const p1 = await leader.next((m) => m.type === "library" && m.part === 1);
check("library parts reach only the asking leader", p0.data[0].id === "a" && p1.data[0].id === "b" && !late.got.some((m) => m.type === "library"));

// The slides computer reconnects: the old socket is replaced, state survives.
const host2 = await open(`${ws}/r/${room}/host?hk=${hostKey}&pin=4321`);
await host2.next((m) => m.type === "clients");
host2.send({ type: "position", data: { itemIdx: 0, slideIdx: 4, itemId: "i1", blankMode: "none" } });
check("reconnected host keeps driving", (await late.next((m) => m.type === "position" && m.data.slideIdx === 4)).data.slideIdx === 4);

// End of service.
host2.send({ type: "close" });
check("devices told the service ended", (await late.next((m) => m.type === "idle")).type === "idle");
const after = await open(`${ws}/r/${room}/ws?k=${key}`);
check("room forgotten after close", (await after.next((m) => m.type === "idle")).type === "idle");

// Reopened (the slides computer keeps one room): devices still on the page
// carry on without rescanning, and the leader's PIN works again.
const reopened = await open(`${ws}/r/${room}/host?hk=${hostKey}&pin=4321`);
await reopened.next((m) => m.type === "clients");
reopened.send({ type: "position", data: { itemIdx: 0, slideIdx: 7, itemId: "i1", blankMode: "none" } });
check("reopened room reaches devices that stayed on the page", (await late.next((m) => m.type === "position" && m.data.slideIdx === 7)).data.slideIdx === 7);
leader.send({ type: "command", command: { kind: "go_to_song", itemId: "i9" } });
check("an unlocked leader keeps working after a reopen", (await reopened.next((m) => m.type === "command")).command.itemId === "i9");

for (const c of [early, host, late, leader, host2, after, reopened]) c.s.close();
console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
