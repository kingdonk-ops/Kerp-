// Forgot password and /reset-password#<token> (T-0053, diff §4.2). Synthetic data only.
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { renderAt } from "../invite/testing";
import { json } from "../test/fakeApi";

const RESET_TOKEN = "syntheticResetToken_0123456789-abcdefABCDEF";
const type = (label: string, value: string) => {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
};
const click = (name: string) => {
  fireEvent.click(screen.getByRole("button", { name }));
};
const newPassword = (password: string) => {
  type(i18n.t("password.form.new"), password);
  type(i18n.t("password.form.confirm"), password);
  click(i18n.t("password.reset.submit"));
};

describe("forgot password", () => {
  it("leads from the sign-in screen to the neutral confirmation (the same for every address)", async () => {
    const { calls } = renderAt("/login", {
      "POST /api/v1/auth/password-reset/request": () => json({ detail: "accepted" }, 202),
    });
    fireEvent.click(await screen.findByRole("link", { name: i18n.t("password.forgot") }));
    await screen.findByRole("heading", { name: i18n.t("password.request.title") });
    type(i18n.t("auth.login.email"), "someone@example.test");
    click(i18n.t("password.request.submit"));

    expect(await screen.findByRole("status")).toHaveTextContent(i18n.t("password.request.sent"));
    expect(calls.find((c) => c.path === "/api/v1/auth/password-reset/request")?.body).toEqual({
      email: "someone@example.test",
    });
  });

  it("says when requests are throttled", async () => {
    renderAt("/forgot-password", {
      "POST /api/v1/auth/password-reset/request": () => new Response(null, { status: 429 }),
    });
    await screen.findByRole("heading", { name: i18n.t("password.request.title") });
    type(i18n.t("auth.login.email"), "someone@example.test");
    click(i18n.t("password.request.submit"));

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("password.throttled"));
  });
});

describe("reset password", () => {
  it("reads the token from the fragment, removes it, sets the password and routes to sign-in", async () => {
    const { calls, router } = renderAt(`/reset-password#${RESET_TOKEN}`, {
      "POST /api/v1/auth/password-reset/confirm": () => json({ status: "password_changed" }),
    });
    await screen.findByRole("heading", { name: i18n.t("password.reset.title") });
    expect(router.state.location.hash).toBe("");
    newPassword("a synthetic long passphrase");

    expect(await screen.findByRole("status")).toHaveTextContent(i18n.t("password.reset.done"));
    expect(screen.getByRole("heading", { name: i18n.t("auth.login.title") })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/auth/password-reset/confirm")?.body).toEqual({
      token: RESET_TOKEN, new_password: "a synthetic long passphrase",
    });
  });

  it.each([
    ["PASSWORD_TOO_SHORT", { min_length: 15, max_length: 256 }, "15"],
    ["PASSWORD_TOO_LONG", { min_length: 15, max_length: 256 }, "256"],
    ["PASSWORD_TOO_COMMON", {}, ""],
  ] as const)("shows %s translated, never the server's detail", async (code, sizes, size) => {
    renderAt(`/reset-password#${RESET_TOKEN}`, {
      "POST /api/v1/auth/password-reset/confirm": () => json({ detail: "raw server text", code, ...sizes }, 422),
    });
    await screen.findByRole("heading", { name: i18n.t("password.reset.title") });
    newPassword("synthetic");

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t(`password.policy.${code}`, { min: size, max: size }));
    expect(document.body.textContent).not.toContain("raw server text");
  });

  it("explains a used or expired link and offers a new one", async () => {
    renderAt(`/reset-password#${RESET_TOKEN}`, {
      "POST /api/v1/auth/password-reset/confirm": () => json({ detail: "gone", code: "RESET_LINK_INVALID" }, 410),
    });
    await screen.findByRole("heading", { name: i18n.t("password.reset.title") });
    newPassword("a synthetic long passphrase");

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("password.reset.linkInvalid"));
    expect(screen.getByRole("link", { name: i18n.t("password.reset.requestNew") })).toHaveAttribute("href", "/forgot-password");
  });

  it("treats a link without a usable token as invalid without calling the server", async () => {
    const { calls } = renderAt("/reset-password");

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("password.reset.linkInvalid"));
    expect(calls.some((c) => c.path.includes("password-reset"))).toBe(false);
  });

  it("ends this tab's session too: the user signs in again with the new password", async () => {
    const { calls } = renderAt(`/reset-password#${RESET_TOKEN}`, {
      "POST /api/v1/auth/password-reset/confirm": () => json({ status: "password_changed" }),
    }, { signedIn: true });
    await screen.findByRole("heading", { name: i18n.t("password.reset.title") });
    newPassword("a synthetic long passphrase");

    expect(await screen.findByRole("status")).toHaveTextContent(i18n.t("password.reset.done"));
    expect(calls.some((c) => c.path === "/api/v1/auth/logout")).toBe(true);
  });
});
