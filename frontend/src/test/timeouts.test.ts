import { getConfig } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ASYNC_UTIL_TIMEOUT_MS, UNIT_TEST_TIMEOUT_MS } from "./timeouts";

describe("test timing budget", () => {
  it("gives findBy*/waitFor the project-wide wait, not the 1 s default", () => {
    expect(getConfig().asyncUtilTimeout).toBe(ASYNC_UTIL_TIMEOUT_MS);
    expect(ASYNC_UTIL_TIMEOUT_MS).toBeGreaterThanOrEqual(5000);
  });

  it("leaves each unit test room for several such waits", () => {
    expect(UNIT_TEST_TIMEOUT_MS).toBeGreaterThanOrEqual(3 * ASYNC_UTIL_TIMEOUT_MS);
  });
});
