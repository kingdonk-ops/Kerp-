import "@testing-library/jest-dom/vitest";
import "../index.css";
import { cleanup, configure } from "@testing-library/react";
import { ASYNC_UTIL_TIMEOUT_MS } from "./timeouts";
import { afterEach } from "vitest";

// findBy*/waitFor wait up to ASYNC_UTIL_TIMEOUT_MS: a screen that appears after mocked
// round-trips must not fail on a loaded CI runner (PR #146/#147 reviews).
configure({ asyncUtilTimeout: ASYNC_UTIL_TIMEOUT_MS });

afterEach(() => {
  cleanup();
});
