/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vite";

/** Accessibility suites (`*.a11y.test.tsx`) need real layout and CSS: they run in Chrome. */
const A11Y_TESTS = "src/**/*.a11y.test.{ts,tsx}";

const chromePath = process.env.KERP_CHROME_PATH;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    globals: false,
    projects: [
      {
        extends: true,
        test: {
          name: "unit",
          environment: "jsdom",
          setupFiles: ["./src/test/setup.ts"],
          include: ["src/**/*.test.{ts,tsx}"],
          exclude: [A11Y_TESTS],
          css: false,
        },
      },
      {
        extends: true,
        test: {
          name: "a11y",
          setupFiles: ["./src/test/setup.browser.ts"],
          include: [A11Y_TESTS],
          css: true,
          browser: {
            enabled: true,
            headless: true,
            // The installed Google Chrome (preinstalled on CI runners): no browser download.
            // KERP_CHROME_PATH points at another Chrome/Chromium binary when needed.
            provider: playwright({
              launchOptions: chromePath ? { executablePath: chromePath } : { channel: "chrome" },
            }),
            instances: [{ browser: "chromium" }],
          },
        },
      },
    ],
  },
});
