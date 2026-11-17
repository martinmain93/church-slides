import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const settings = { share_usage: null as boolean | null };
vi.mock("@/lib/invoke", () => ({ isInTauri: () => true }));
vi.mock("@/lib/tauri", () => ({
  getSettings: () => Promise.resolve({ ...settings }),
  getAppIdentity: () =>
    Promise.resolve({ install_id: "01JA2B3C4D5E6F7G8H9JKMNPQR", version: "0.0.9", os: "macos", os_version: "15.1.0", arch: "aarch64" }),
}));

import { recordUsage, ServiceTally, statsUrl, updateCheckHeaders } from "@/lib/usage";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("DEV", false);
  vi.stubEnv("VITE_CHURCH_SLIDES_STATS_URL", "https://stats.example.test");
  localStorage.clear();
  settings.share_usage = null;
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: true, status: 204 });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("usage statistics", () => {
  it("sends nothing until the user says yes", async () => {
    await recordUsage("service", { minutes: 70 });
    settings.share_usage = false;
    await recordUsage("service", { minutes: 70 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends a batch with the install id once agreed", async () => {
    settings.share_usage = true;
    await recordUsage("service", { minutes: 70 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${statsUrl()}/v1/events`);
    expect(JSON.parse(init.body as string)).toEqual({
      install_id: "01JA2B3C4D5E6F7G8H9JKMNPQR",
      version: "0.0.9",
      events: [{ name: "service", props: { minutes: 70 } }],
    });
  });

  it("keeps events while offline and sends them later", async () => {
    settings.share_usage = true;
    fetchMock.mockRejectedValueOnce(new TypeError("Load failed"));
    await recordUsage("service", { minutes: 70 });
    await recordUsage("app_open", { songs: 12 });
    const body = JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string);
    expect(body.events.map((e: { name: string }) => e.name)).toEqual(["service", "app_open"]);
  });

  it("sends nothing from a build with no stats address (anyone's own build)", async () => {
    vi.stubEnv("VITE_CHURCH_SLIDES_STATS_URL", "");
    settings.share_usage = true;
    await recordUsage("service", { minutes: 70 });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(await updateCheckHeaders()).toBeUndefined();
  });

  it("sends nothing from a dev build", async () => {
    vi.stubEnv("DEV", true);
    settings.share_usage = true;
    await recordUsage("service", { minutes: 70 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("update-check headers", () => {
  it("carry the install's identity when sharing usage statistics", async () => {
    settings.share_usage = true;
    expect(await updateCheckHeaders()).toEqual({
      "X-Install-Id": "01JA2B3C4D5E6F7G8H9JKMNPQR",
      "X-App-Version": "0.0.9",
      "X-OS": "macos",
      "X-OS-Version": "15.1.0",
      "X-Arch": "aarch64",
    });
  });

  it("are left off until the user agrees, and after they say no", async () => {
    expect(await updateCheckHeaders()).toBeUndefined();
    settings.share_usage = false;
    expect(await updateCheckHeaders()).toBeUndefined();
  });
});

describe("ServiceTally", () => {
  const MIN = 60_000;

  it("counts changes by who made them, songs, and AI time", () => {
    const t = new ServiceTally("operator", 0);
    t.slideShown(0, null); // the opening slide
    t.slideShown(0, "manual");
    t.aiMode(true, 5 * MIN);
    t.slideShown(1, "ai");
    t.slideShown(1, "ai");
    t.aiMode(false, 35 * MIN);
    t.slideShown(2, "voice");
    expect(t.finish(70 * MIN)).toEqual({
      screen: "operator",
      minutes: 70,
      songs: 3,
      ai_minutes: 30,
      by_ai: 2,
      by_hand: 1,
      by_voice: 1,
      continuous: false,
    });
  });

  it("counts AI time still running at the end", () => {
    const t = new ServiceTally("single", 0);
    t.aiMode(true, 0);
    t.displayContinuous();
    expect(t.finish(10 * MIN)).toMatchObject({ ai_minutes: 10, continuous: true });
  });

  it("skips a service too short to be one", () => {
    expect(new ServiceTally("single", 0).finish(MIN)).toBeNull();
  });
});
