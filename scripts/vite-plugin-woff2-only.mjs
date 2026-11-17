/**
 * Drop the WOFF fallback from Fontsource's `@font-face` rules, so a build
 * bundles each font once, as WOFF2.
 *
 * Every Fontsource stylesheet lists `url(x.woff2) format('woff2'), url(x.woff)
 * format('woff')`, and Vite bundles every file a stylesheet names whether or
 * not a browser would ever fetch it: 47 WOFF copies, 1.1 MB, that no engine
 * the app targets (Safari 14, Chrome 105 — see `build.target`) would load.
 *
 * Plain JavaScript (typed by the .d.mts beside it) for the same reason as
 * vite-plugin-bundled-packages.mjs.
 */

const FONTSOURCE = /[\\/]@fontsource[\\/].+\.css$/;
const WOFF_FALLBACK = /,\s*url\([^)]*\.woff\)\s*format\(\s*['"]woff['"]\s*\)/g;

/** @returns {import("vite").Plugin} */
export function woff2Only() {
  return {
    name: "woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!FONTSOURCE.test(id.split("?")[0])) return null;
      return { code: code.replace(WOFF_FALLBACK, ""), map: null };
    },
  };
}
