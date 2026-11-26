// The web app (app.churchslides.app): a static site, built into web/dist.
//   pnpm web:dev     http://localhost:1430 (add ?server=http://localhost:8787 for a local server)
//   pnpm web:build
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

import { bundledPackages } from "../scripts/vite-plugin-bundled-packages.mjs";
import { woff2Only } from "../scripts/vite-plugin-woff2-only.mjs";

export default defineConfig({
  root: __dirname,
  plugins: [react(), woff2Only(), bundledPackages("web")],
  resolve: { alias: { "@": path.resolve(__dirname, "../src") } },
  css: { postcss: path.resolve(__dirname, "..") },
  build: {
    outDir: path.resolve(__dirname, "dist"),
    emptyOutDir: true,
    // Phones from the last few years (as the band page).
    target: ["es2020", "safari14", "chrome105"],
  },
  server: { port: 1430, strictPort: true },
});
