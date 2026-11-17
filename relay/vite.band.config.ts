// Builds the band page into one JS file and one CSS file for the relay Worker.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

import { bundledPackages } from "../scripts/vite-plugin-bundled-packages.mjs";
import { woff2Only } from "../scripts/vite-plugin-woff2-only.mjs";

export default defineConfig({
  plugins: [react(), woff2Only(), bundledPackages("band")],
  resolve: { alias: { "@": path.resolve(__dirname, "../src") } },
  css: { postcss: path.resolve(__dirname, "..") },
  build: {
    outDir: path.resolve(__dirname, "dist/band"),
    emptyOutDir: true,
    cssCodeSplit: false,
    // Phones from the last few years; the band page is the only code here.
    target: "es2019",
    rollupOptions: {
      input: path.resolve(__dirname, "band-main.tsx"),
      output: { inlineDynamicImports: true, entryFileNames: "band.js", assetFileNames: "band[extname]" },
    },
  },
});
