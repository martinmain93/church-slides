import { describe, expect, it } from "vitest";

import {
  downloadTarget,
  isInstallId,
  osFromUserAgent,
  parseUsageBatch,
  passwordMatches,
  referrerHost,
  updateCheckFrom,
} from "../src/validate";

const ID = "01JA2B3C4D5E6F7G8H9JKMNPQR";

describe("stats request checks", () => {
  it("accepts only ULID install ids", () => {
    expect(isInstallId(ID)).toBe(true);
    expect(isInstallId("01ja2b3c4d5e6f7g8h9jkmnpqr")).toBe(false);
    expect(isInstallId("someone@example.com")).toBe(false);
    expect(isInstallId(undefined)).toBe(false);
  });

  it("maps download links to GitHub, newest or a given release", () => {
    const base = "https://github.com/martinmain93/church-slides-releases/releases";
    expect(downloadTarget("/dl/Church-Slides-intel.dmg")?.url).toBe(`${base}/latest/download/Church-Slides-intel.dmg`);
    expect(downloadTarget("/dl/v0.0.4/Church-Slides-windows-setup.exe")?.url).toBe(
      `${base}/download/v0.0.4/Church-Slides-windows-setup.exe`,
    );
    expect(downloadTarget("/dl/evil.exe")).toBeNull();
    expect(downloadTarget("/dl/../Church-Slides-intel.dmg")).toBeNull();
    expect(downloadTarget("/dl/main/Church-Slides-intel.dmg")).toBeNull();
  });

  it("counts an update check only when it carries an install id", () => {
    expect(updateCheckFrom(new Headers({ "x-app-version": "0.0.9" }))).toBeNull();
    const check = updateCheckFrom(
      new Headers({ "x-install-id": ID, "x-app-version": "0.0.9", "x-os": "macos", "x-os-version": "15.1.0", "x-arch": "aarch64" }),
    );
    expect(check).toEqual({ id: ID, version: "0.0.9", os: "macos", osVersion: "15.1.0", arch: "aarch64" });
  });

  it("drops header values that aren't short labels", () => {
    const check = updateCheckFrom(new Headers({ "x-install-id": ID, "x-os-version": "<script>" }));
    expect(check?.osVersion).toBeNull();
  });

  it("keeps only the referrer's host", () => {
    expect(referrerHost("https://example.org/path?who=me")).toBe("example.org");
    expect(referrerHost("not a url")).toBeNull();
    expect(referrerHost(null)).toBeNull();
  });

  it("reads the OS family from a user agent", () => {
    expect(osFromUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")).toBe("mac");
    expect(osFromUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(osFromUserAgent(null)).toBe("other");
  });

  it("accepts a well-formed usage batch", () => {
    const batch = parseUsageBatch({
      install_id: ID,
      version: "0.0.9",
      events: [{ name: "service", props: { screen: "operator", minutes: 70, continuous: false } }],
    });
    expect(batch).toEqual({
      installId: ID,
      version: "0.0.9",
      events: [{ name: "service", props: { screen: "operator", minutes: 70, continuous: false } }],
    });
  });

  it("refuses free text, nesting and oversized batches", () => {
    const base = { install_id: ID, version: "0.0.9" };
    expect(parseUsageBatch({ ...base, events: [{ name: "x", props: { note: "a".repeat(61) } }] })).toBe("bad value note");
    expect(parseUsageBatch({ ...base, events: [{ name: "x", props: { nested: { a: 1 } } }] })).toBe("bad value nested");
    expect(parseUsageBatch({ ...base, events: [{ name: "Bad Name" }] })).toBe("bad event name");
    expect(parseUsageBatch({ ...base, events: Array(21).fill({ name: "x" }) })).toBe("too many events");
    expect(parseUsageBatch({ ...base, events: [] })).toBe("no events");
    expect(parseUsageBatch({ install_id: "nope", events: [{ name: "x" }] })).toBe("bad install_id");
  });

  it("checks the stats password", () => {
    const auth = (pw: string) => `Basic ${btoa(`martin:${pw}`)}`;
    expect(passwordMatches(auth("secret"), "secret")).toBe(true);
    expect(passwordMatches(auth("wrong!"), "secret")).toBe(false);
    expect(passwordMatches(auth(""), "")).toBe(false); // unset password: locked
    expect(passwordMatches(null, "secret")).toBe(false);
  });
});
