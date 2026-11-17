import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

import { bundledPackages } from "./scripts/vite-plugin-bundled-packages.mjs";
import { woff2Only } from "./scripts/vite-plugin-woff2-only.mjs";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig(async () => ({
  // bundledPackages: what the third-party notices list (scripts/third_party_notices.py).
  // woff2Only: each font once, as WOFF2 (see the plugin).
  plugins: [react(), woff2Only(), bundledPackages("app")],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Tauri-specific dev server config:
  // - Fixed port for the desktop app to attach to
  // - Don't try to open a browser
  // - Listen on all interfaces, so phones on the same Wi-Fi can load the
  //   band screens in dev (the band server on :7777 redirects them here —
  //   see `serve_page` in src-tauri/src/band.rs). Release builds don't use
  //   this server at all; the band server serves the bundled app itself.
  // The oldest engines the app ships to, matching `browserslist` in
  // package.json: Safari 14 is the web view on a macOS 10.15/11 Mac, Chrome
  // 105 the oldest WebView2 we'd meet on Windows 10. Pinned rather than left
  // to Vite's default so a Vite upgrade can't raise it quietly.
  build: {
    target: ["es2020", "safari14", "chrome105"],
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || "0.0.0.0",
    hmr: host
      ? { protocol: "ws", host, port: 1421 }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**", "**/sidecar/**"],
    },
  },
}));
