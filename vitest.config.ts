import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    globals: true,
    css: false,
    // Agent worktrees under `.claude/` are full checkouts with their own
    // `node_modules`. Left in the glob they contribute stale copies of these
    // same test files, and a second React instance that fails every render
    // with a null-dispatcher error unrelated to anything in `src/`.
    exclude: ["**/node_modules/**", "**/dist/**", ".claude/**", "research/**", "build/**"],
  },
});
