// Invite link (T-0053, diff §4.1): token from the fragment, removed from the address bar, sent
// only in POST bodies; join when signed in; carried through sign-in otherwise; first password
// from the e-mailed link. Synthetic data only.
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { scopeStorageKey } from "../projects/ProjectScopeProvider";
import { FAKE_USER, json, tokens } from "../test/fakeApi";
import { parseInviteFragment, PENDING_INVITE_KEY } from "./pendingInvite";
import { INVITE_TOKEN, JOINED, JOINED_LABEL, renderAt, SETUP_TOKEN } from "./testing";

const PREVIEW = {
  project_id: JOINED,
  project_code: "SYN-002",
  project_name: "Beta Synthetic Tank Farm",
  inviter_name: "Synthetic Admin",
};
const refusal = (code: string, status: number) => () => json({ detail: "Synthetic refusal", code }, status);
const signedInJoin = { "POST /api/v1/admin/invites/preview": () => json(PREVIEW) };
const scopeIndicator = () => screen.findByTestId("scope-indicator");
const click = (name: string) => {
  fireEvent.click(screen.getByRole("button", { name }));
};
const type = (label: string, value: string) => {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("parseInviteFragment", () => {
  it("reads the invite token and the optional setup token", () => {
    expect(parseInviteFragment(`#${INVITE_TOKEN}`)).toEqual({ inviteToken: INVITE_TOKEN, setupToken: null });
    expect(parseInviteFragment(`#${INVITE_TOKEN}.${SETUP_TOKEN}`)).toEqual({ inviteToken: INVITE_TOKEN, setupToken: SETUP_TOKEN });
  });

  it.each(["", "#", "#short", `#${INVITE_TOKEN}.`, `#${INVITE_TOKEN}.a.b`, "#<script>alert(1)</script>xxxxxxxx"])(
    "rejects %j",
    (hash) => {
      expect(parseInviteFragment(hash)).toBeNull();
    },
  );
});

describe("signed in", () => {
  it("removes the token from the address bar, previews and joins in one click, then opens the project", async () => {
    const { calls, router, preferences, session } = renderAt(`/invite#${INVITE_TOKEN}`, {
      ...signedInJoin,
      "POST /api/v1/admin/invites/accept": () => json({ joined_project_id: JOINED }),
    }, { signedIn: true });

    expect(await screen.findByText(i18n.t("invite.join.body", { project: JOINED_LABEL, inviter: "Synthetic Admin" }))).toBeInTheDocument();
    expect(router.state.location.hash).toBe("");
    click(i18n.t("invite.join.submit"));

    expect(await scopeIndicator()).toHaveTextContent(JOINED_LABEL);
    expect(preferences.getItem(scopeStorageKey(FAKE_USER.id))).toBe(`project:${JOINED}`);
    expect(session.getItem(PENDING_INVITE_KEY)).toBeNull();
    const posts = calls.filter((c) => c.path.startsWith("/api/v1/admin/invites/"));
    expect(posts.map((c) => [c.method, c.body])).toEqual([
      ["POST", { invite_token: INVITE_TOKEN }],
      ["POST", { invite_token: INVITE_TOKEN }],
    ]);
    // The token never travels in a URL.
    expect(calls.every((c) => !c.path.includes(INVITE_TOKEN))).toBe(true);
  });

  it("tells the wrong user neutrally and offers sign-out into the invite sign-in", async () => {
    renderAt(`/invite#${INVITE_TOKEN}`, {
      "POST /api/v1/admin/invites/preview": refusal("INVITE_WRONG_USER", 403),
    }, { signedIn: true });

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("invite.problem.wrongUser"));
    expect(screen.getByRole("link", { name: i18n.t("invite.toLobby") })).toHaveAttribute("href", "/lobby");
    click(i18n.t("invite.signOut"));

    expect(await screen.findByText(i18n.t("invite.signIn.notice"))).toBeInTheDocument();
  });

  it.each([
    ["expired, revoked or used", refusal("INVITE_GONE", 410), "gone"],
    ["accepted in parallel", refusal("INVITE_CONFLICT", 409), "gone"],
    ["unexpected", () => json({ detail: "<b>internal</b>" }, 500), "unavailable"],
  ] as const)("shows a neutral message with the lobby link when the invite is %s", async (_label, accept, problem) => {
    const { session } = renderAt(`/invite#${INVITE_TOKEN}`, {
      ...signedInJoin,
      "POST /api/v1/admin/invites/accept": accept,
    }, { signedIn: true });
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("invite.join.submit") }));

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t(`invite.problem.${problem}`));
    expect(document.body.textContent).not.toContain("internal");
    expect(screen.queryByRole("button", { name: i18n.t("invite.signOut") })).toBeNull();
    expect(session.getItem(PENDING_INVITE_KEY)).toBeNull();
  });

  it("treats a malformed fragment as an unusable invite without calling the server", async () => {
    const { calls } = renderAt("/invite#not-a-token", {}, { signedIn: true });

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("invite.problem.gone"));
    expect(calls.some((c) => c.path.includes("/invites/"))).toBe(false);
  });
});

describe("not signed in", () => {
  const login = (body: Record<string, unknown>) => ({
    "POST /api/v1/auth/login": () => json({ ...tokens(1), ...body }),
  });

  it("carries the token through password sign-in and lands in the joined project", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) => vi.spyOn(console, level));
    const { calls, router, preferences, session } = renderAt(`/invite#${INVITE_TOKEN}`, login({ joined_project_id: JOINED }));

    expect(await screen.findByText(i18n.t("invite.signIn.notice"))).toBeInTheDocument();
    expect(router.state.location.hash).toBe("");
    expect(session.getItem(PENDING_INVITE_KEY)).toContain(INVITE_TOKEN);
    type(i18n.t("auth.login.email"), FAKE_USER.email);
    type(i18n.t("auth.login.password"), "secret");
    click(i18n.t("auth.login.submit"));

    expect(await scopeIndicator()).toHaveTextContent(JOINED_LABEL);
    expect(calls.find((c) => c.path === "/api/v1/auth/login")?.body).toEqual({
      email: FAKE_USER.email, password: "secret", invite_token: INVITE_TOKEN,
    });
    expect(session.getItem(PENDING_INVITE_KEY)).toBeNull();
    // Never in long-lived storage or logs.
    expect(preferences.getItem(scopeStorageKey(FAKE_USER.id))).toBe(`project:${JOINED}`);
    expect(JSON.stringify(localStorage)).not.toContain(INVITE_TOKEN);
    for (const spy of spies) for (const args of spy.mock.calls) expect(JSON.stringify(args)).not.toContain(INVITE_TOKEN);
  });

  it("shows the neutral message after a sign-in whose invite was refused", async () => {
    renderAt(`/invite#${INVITE_TOKEN}`, login({ invite_error: "INVITE_GONE" }));
    await screen.findByText(i18n.t("invite.signIn.notice"));
    type(i18n.t("auth.login.email"), FAKE_USER.email);
    type(i18n.t("auth.login.password"), "secret");
    click(i18n.t("auth.login.submit"));

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("invite.problem.gone"));
    expect(screen.getByRole("link", { name: i18n.t("invite.toLobby") })).toBeInTheDocument();
  });

  it("posts the token in the Microsoft form body, never in its URL", async () => {
    const startUrl = "/api/v1/auth/sso/entra/start";
    renderAt(`/invite#${INVITE_TOKEN}`, {
      "GET /api/v1/auth/config": () => json({ password: { available: true }, sso: { provider: "entra", preferred: true, start_url: startUrl } }),
    });
    const form = (await screen.findByRole("button", { name: i18n.t("auth.sso.start") })).closest("form");

    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", startUrl);
    expect(form?.querySelector<HTMLInputElement>('input[name="invite_token"]')?.value).toBe(INVITE_TOKEN);
  });

  it.each([
    ["INVITE_WRONG_USER", "wrongUser"],
    ["<img src=x>", "unavailable"],
  ] as const)("shows the neutral message for invite_error=%s after the SSO round trip", async (code, problem) => {
    const { session } = renderAt(`/login/sso-return?status=signed_in&invite_error=${encodeURIComponent(code)}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t(`invite.problem.${problem}`));
    expect(document.body.innerHTML).not.toContain("<img");
    expect(session.getItem(PENDING_INVITE_KEY)).toBeNull();
  });

  it("requests a magic link with the token and explains what to do if no e-mail arrives", async () => {
    const { calls } = renderAt(`/invite#${INVITE_TOKEN}`, {
      "POST /api/v1/auth/magic-link": () => json({ detail: "accepted" }, 202),
    });
    await screen.findByText(i18n.t("invite.signIn.notice"));
    type(i18n.t("auth.login.email"), "fitter@example.test");
    click(i18n.t("invite.magicLink.submit"));

    expect(await screen.findByText(i18n.t("invite.magicLink.sent"))).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/v1/auth/magic-link")?.body).toEqual({
      email: "fitter@example.test", invite_token: INVITE_TOKEN,
    });
  });

  it("offers 'Send me an e-mail to set my password' for a copied link", async () => {
    renderAt(`/invite#${INVITE_TOKEN}`);
    fireEvent.click(await screen.findByRole("link", { name: i18n.t("invite.signIn.setPassword") }));

    expect(await screen.findByRole("heading", { name: i18n.t("password.request.setupTitle") })).toBeInTheDocument();
  });

  it("drops a pending invite when the sign-in screen is opened outside its flow", async () => {
    const { session, router } = renderAt(`/invite#${INVITE_TOKEN}`);
    await screen.findByText(i18n.t("invite.signIn.notice"));
    void router.navigate("/login");

    await waitFor(() => {
      expect(session.getItem(PENDING_INVITE_KEY)).toBeNull();
    });
    expect(screen.queryByText(i18n.t("invite.signIn.notice"))).toBeNull();
  });
});

describe("e-mailed link of an account without credentials", () => {
  const setup = (canSet: boolean) => ({
    "POST /api/v1/admin/invites/setup-preview": () =>
      json(canSet ? { can_set_password: true, project_code: "SYN-002", project_name: "Beta Synthetic Tank Farm" } : { can_set_password: false }),
  });
  const fill = (password: string, confirm = password) => {
    type(i18n.t("password.form.new"), password);
    type(i18n.t("password.form.confirm"), confirm);
    click(i18n.t("invite.setup.submit"));
  };

  it("creates the password, joins and lands in the project; no Microsoft button", async () => {
    let attempt = 0;
    const { calls } = renderAt(`/invite#${INVITE_TOKEN}.${SETUP_TOKEN}`, {
      ...setup(true),
      "GET /api/v1/auth/config": () => json({ password: { available: true }, sso: { provider: "entra", preferred: true, start_url: "/api/v1/auth/sso/entra/start" } }),
      "POST /api/v1/admin/invites/accept-with-password": () =>
        ++attempt === 1
          ? json({ detail: "too short", code: "PASSWORD_TOO_SHORT", min_length: 15, max_length: 256 }, 422)
          : json({ ...tokens(1), joined_project_id: JOINED }),
    });

    expect(await screen.findByRole("heading", { name: i18n.t("invite.setup.title") })).toBeInTheDocument();
    expect(screen.getByText(i18n.t("invite.setup.project", { project: JOINED_LABEL }))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: i18n.t("auth.sso.start") })).toBeNull();
    fill("short");
    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("password.policy.PASSWORD_TOO_SHORT", { min: 15 }));
    expect(screen.getByText(i18n.t("password.form.lengthMin", { n: 5, min: 15 }))).toBeInTheDocument();
    fill("a synthetic long passphrase");

    expect(await scopeIndicator()).toHaveTextContent(JOINED_LABEL);
    expect(calls.filter((c) => c.path === "/api/v1/admin/invites/accept-with-password").at(-1)?.body).toEqual({
      invite_token: INVITE_TOKEN, setup_token: SETUP_TOKEN, new_password: "a synthetic long passphrase",
    });
  });

  it("refuses a confirmation that differs without calling the server", async () => {
    const { calls } = renderAt(`/invite#${INVITE_TOKEN}.${SETUP_TOKEN}`, setup(true));
    await screen.findByRole("heading", { name: i18n.t("invite.setup.title") });
    fill("one synthetic phrase", "another synthetic phrase");

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("password.form.mismatch"));
    expect(calls.some((c) => c.path.endsWith("accept-with-password"))).toBe(false);
  });

  it("sends an account that has credentials to the invite sign-in", async () => {
    renderAt(`/invite#${INVITE_TOKEN}.${SETUP_TOKEN}`, setup(false));

    expect(await screen.findByText(i18n.t("invite.signIn.notice"))).toBeInTheDocument();
  });

  it("shows the neutral message for a gone link", async () => {
    renderAt(`/invite#${INVITE_TOKEN}.${SETUP_TOKEN}`, {
      "POST /api/v1/admin/invites/setup-preview": refusal("INVITE_GONE", 410),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("invite.problem.gone"));
  });
});
