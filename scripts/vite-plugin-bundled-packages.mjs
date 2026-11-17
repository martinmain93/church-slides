/**
 * Records which npm packages a production build actually bundles, for the
 * app's third-party notices (scripts/third_party_notices.py). Writes
 * `build/notices/js-<name>.json`: the sorted package folders.
 *
 * Plain JavaScript (typed by the .d.mts beside it) so `tsc -b` doesn't emit
 * a compiled copy next to it, as it does for vite.config.ts.
 *
 * Asking the bundle rather than package.json keeps build tooling out of the
 * notices (tailwindcss-animate's peer dependency on tailwindcss alone pulls
 * ~80 packages into `pnpm licenses list --prod`) and catches anything the
 * interface imports that package.json doesn't name directly.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MARK = "/node_modules/";

/** @param {string} name @returns {import("vite").Plugin} */
export function bundledPackages(name) {
  return {
    name: "bundled-packages",
    apply: "build",
    generateBundle() {
      const roots = new Set();
      for (const id of this.getModuleIds()) {
        const file = id.replace(/^\0/, "").split("?")[0].replace(/\\/g, "/");
        const at = file.lastIndexOf(MARK);
        if (at < 0) continue;
        const parts = file.slice(at + MARK.length).split("/");
        const pkg = parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
        roots.add(file.slice(0, at + MARK.length) + pkg);
      }
      const out = path.join(ROOT, "build", "notices", `js-${name}.json`);
      mkdirSync(path.dirname(out), { recursive: true });
      writeFileSync(out, JSON.stringify([...roots].sort(), null, 2) + "\n");
    },
  };
}
