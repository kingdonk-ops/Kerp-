import { describe, expect, it } from "vitest";

import { safeStartUrl, ssoErrorCode, SSO_ERROR_CODES } from "./sso";
import { memoryStorage, SESSION_STORAGE_KEY, TokenStore } from "./tokenStore";

describe("ssoErrorCode", () => {
  it.each(SSO_ERROR_CODES)("keeps the known code %s", (code) => {
    expect(ssoErrorCode(code)).toBe(code);
  });

  it.each([null, undefined, "", "sso_no_account", "eyJhbGciOiJSUzI1NiJ9.e30.sig", 42])(
    "maps %s to the generic refusal",
    (raw) => {
      expect(ssoErrorCode(raw)).toBe("SSO_FAILED");
    },
  );
});

describe("safeStartUrl", () => {
  const sso = (start_url: string) => ({
    sso: { provider: "entra" as const, preferred: true, start_url },
  });

  it("is null without SSO", () => {
    expect(safeStartUrl(undefined)).toBeNull();
    expect(safeStartUrl({})).toBeNull();
    expect(safeStartUrl({ sso: null })).toBeNull();
  });

  it("accepts the same-origin API path", () => {
    expect(safeStartUrl(sso("/api/v1/auth/sso/entra/start"))).toBe(
      "/api/v1/auth/sso/entra/start",
    );
  });

  it.each([
    "https://evil.example/api/v1/auth/sso/entra/start",
    "//evil.example/api/v1/auth/sso/entra/start",
    "/api//evil.example",
    "/api/\\evil.example",
    "javascript:alert(1)",
    "/login",
  ])("refuses %s", (url) => {
    expect(safeStartUrl(sso(url))).toBeNull();
  });
});

describe("TokenStore.adoptCookieSession", () => {
  it("marks a session to resume with the cookie and drops a stale access token", () => {
    const storage = memoryStorage();
    const store = new TokenStore(storage);
    store.set({ access_token: "access-old" });
    let notified = 0;
    store.subscribe(() => {
      notified += 1;
    });

    store.adoptCookieSession();

    expect(store.access).toBeNull();
    expect(store.hasSession).toBe(true);
    expect(storage.getItem(SESSION_STORAGE_KEY)).toBe("1");
    expect(notified).toBe(1);
  });
});
