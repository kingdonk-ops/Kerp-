// NFR performance job (T-0045, diff §6.3): `npm run perf`. Separate from `npm run test` so the
// unit and a11y suites stay fast; CI runs it in its own `perf` job on every PR (warn-only until
// T-0146). Budgets, CPU throttle and network profile: `src/perf/budgets.json`.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

const chromePath = process.env.KERP_CHROME_PATH;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    name: "perf",
    globals: false,
    include: ["src/**/*.perf.{ts,tsx}"],
    setupFiles: ["./src/test/setup.browser.ts"],
    css: true,
    // One measurement at a time: parallel files would compete for the CPU.
    fileParallelism: false,
    testTimeout: 120_000,
    reporters: ["default", "./src/perf/reporter.ts"],
    browser: {
      enabled: true,
      headless: true,
      // Same browser as the a11y suites: installed Google Chrome, or KERP_CHROME_PATH (TD-260).
      // CPU throttling goes through the Chrome DevTools Protocol (`cdp()`), Chromium only.
      provider: playwright({
        launchOptions: chromePath ? { executablePath: chromePath } : { channel: "chrome" },
      }),
      instances: [{ browser: "chromium" }],
    },
  },
});
