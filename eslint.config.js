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
);
