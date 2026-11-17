import { describe, expect, it } from "vitest";

import { i18n } from "./index";

/**
 * The `error` codes the Entra ID SSO callback redirects with (T-0054,
 * `backend/app/core/auth/entra.py` CLIENT_CODES + DEVICE_REVOKED). Each needs a message.
 */
const SSO_ERROR_CODES = [
  "SSO_GUEST",
  "SSO_NO_ACCOUNT",
  "SSO_ACCOUNT_DEACTIVATED",
  "SSO_EMAIL_UNVERIFIED",
  "SSO_ACCOUNT_LINKED_ELSEWHERE",
  "SSO_FAILED",
  "DEVICE_REVOKED",
] as const;

describe("SSO refusal messages", () => {
  it.each(SSO_ERROR_CODES)("translates %s", (code) => {
    const key = `auth.sso.errors.${code}`;
    expect(i18n.exists(key)).toBe(true);
    expect(i18n.t(key, { defaultValue: key })).not.toBe(key);
  });

  it("tells a guest to use the method from the invitation", () => {
    expect(i18n.t("auth.sso.errors.SSO_GUEST")).toMatch(/method from your invitation/);
    expect(i18n.t("auth.sso.errors.SSO_NO_ACCOUNT")).toMatch(/ask your administrator/);
  });
});
