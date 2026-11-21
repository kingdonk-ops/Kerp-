// ESLint flat config of the e2e suite (T-0027): type-aware typescript-eslint, plus the
// suite's own rule: no fixed sleeps (wait on what the user sees or a response instead).
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig(
  { ignores: ["playwright-report/**", "test-results/**"] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        projectService: {
          allowDefaultProject: ["eslint.config.js", "vite.preview.config.mjs"],
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      "no-restricted-properties": [
        "error",
        { property: "waitForTimeout", message: "No fixed sleeps: wait for a locator or response." },
      ],
    },
  },
);
