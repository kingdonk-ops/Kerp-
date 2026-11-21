import { describe, expect, it } from "vitest";

import { SSO_ERROR_CODES } from "../auth/sso";
import { i18n } from "./index";

/** The callback's `error` codes (T-0054) are listed once, in `src/auth/sso.ts`. */
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
