// `vite preview` of the built frontend for the e2e suite (T-0027): serves frontend/dist and
// proxies the API paths to the API, so the app talks to its own origin as in production.
// Started by playwright.config.ts, which sets both variables. No imports beyond Node's own:
// vite loads this file from the frontend's installation.
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const base = new URL(process.env.KERP_E2E_BASE_URL ?? "");
const api = process.env.KERP_E2E_API_URL ?? "";
if (!api) throw new Error("KERP_E2E_API_URL must be set");

export default {
  root: path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "frontend"),
  preview: {
    host: base.hostname,
    port: Number(base.port),
    strictPort: true,
    // Same origin as the SPA: the refresh cookie and the Origin check see the web origin.
    proxy: {
      "/api": { target: api },
      "/.well-known": { target: api },
    },
  },
};
