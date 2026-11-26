// The app's lint: TypeScript's recommended rules plus the React hooks rules.
// `tsc` already reports unused names, so ESLint doesn't repeat it.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "build/**",
      "site/**",
      "relay/**",
      "web/dist/**",
      "stats/**",
      "src-tauri/**",
      "sidecar/**",
      "research/**",
      "node_modules/**",
      ".claude/**",
      "src/types/generated.ts",
      "**/*.d.ts",
      ".tsbuild/**",
    ],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
  // Layers (docs/roadmap/WEB_AND_MOBILE.md): `core` is shared by every
  // client, so it imports no UI or platform; the library interface imports no
  // platform. Each platform's implementation lives beside it.
  {
    files: ["src/core/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["react", "react-dom", "react/*"], message: "src/core has no UI: it's shared by every client." },
            { group: ["@tauri-apps/*"], message: "src/core runs on every platform, not only the desktop." },
            { group: ["@/lib/*", "@/components/*", "@/library/*"], message: "src/core imports only from src/core." },
          ],
        },
      ],
    },
  },
  {
    files: ["src/library/source.ts", "src/library/context.tsx"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["@tauri-apps/*", "@/lib/tauri", "@/lib/invoke"], message: "Platform code goes in its own implementation (desktop.ts)." },
          ],
        },
      ],
    },
  },
  // The web app and its library run in a browser: nothing from Tauri, and
  // nothing from the desktop's library either.
  {
    files: ["src/web/**/*.{ts,tsx}", "src/library/web/**/*.{ts,tsx}", "src/lib/account/**/*.ts", "web/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@tauri-apps/*", "@/lib/tauri", "@/lib/invoke", "@/library/desktop", "@/lib/displayLook", "@/lib/serviceItems"],
              message: "The web app runs in a browser: no Tauri, directly or through desktop modules.",
            },
          ],
        },
      ],
    },
  },
);
