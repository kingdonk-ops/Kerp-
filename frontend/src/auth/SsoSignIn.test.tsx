import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { SsoSignIn } from "./SsoSignIn";

const START_URL = "/api/v1/auth/sso/entra/start";

function renderButton(inviteToken?: string) {
  render(
    <I18nextProvider i18n={i18n}>
      <SsoSignIn startUrl={START_URL} inviteToken={inviteToken} />
    </I18nextProvider>,
  );
  const form = screen.getByRole("button", { name: i18n.t("auth.sso.start") }).closest("form");
  if (!form) throw new Error("no form");
  return form;
}

describe("SsoSignIn", () => {
  it("navigates to the start URL without a body when no invite is pending", () => {
    const form = renderButton();
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", START_URL);
    expect(form.querySelectorAll("input")).toHaveLength(0);
  });

  it("posts a pending invite token in the form body, never in the URL (T-0053)", () => {
    const token = "synthetic-invite-token";
    const form = renderButton(token);
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", START_URL);
    expect(form.getAttribute("action")).not.toContain(token);
    const input = form.querySelector('input[name="invite_token"]');
    expect(input).toHaveAttribute("type", "hidden");
    expect(input).toHaveValue(token);
  });
});
