// Admin → Users (T-0331, diff §4.1, §6.1): accounts for System admins; invites by e-mail for
// PER-09 holders and System admins. Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import type { ProjectSummary } from "../projects/directory";
import { FAKE_USER, json, type Recorded } from "../test/fakeApi";
import type { OpenInvite } from "./invites";
import { ADMIN_PROJECT, project, renderAdmin } from "./testing";
import { formatTime } from "./ui";

// Waits after mocked round-trips through the whole shell (src/test/timeouts.ts budget).
const WAIT = { timeout: 5000 };
const PATH = "/admin/users";
const P = ADMIN_PROJECT.id;
const OTHER_PROJECT: ProjectSummary = {
  id: "00000000-0000-4000-8000-000000000102",
  code: "SYN-002",
  name: "Synthetic Tank Farm",
  personas: ["PER-01"],
  access: "member",
};
const ME = { id: FAKE_USER.id, email: FAKE_USER.email, display_name: FAKE_USER.display_name, is_active: true, is_internal: true };
const PLANNER = { id: "00000000-0000-4000-8000-000000000302", email: "planner@example.test", display_name: "Synthetic Planner", is_active: true, is_internal: true };
const LEAVER = { id: "00000000-0000-4000-8000-000000000303", email: "leaver@example.test", display_name: "Synthetic Leaver", is_active: false, is_internal: false };
const TOKEN = "syntheticCreatedInviteToken_0123456789";
const LINK = `http://kerp.test/invite#${TOKEN}`;
const INVITES = `/api/v1/admin/projects/${P}/invites`;
const INVITE: OpenInvite = {
  id: "00000000-0000-4000-8000-000000000601",
  email: "client@example.test",
  grants: [{ persona: "PER-15", site_node_id: null }],
  status: "pending",
  expires_at: "2026-11-28T08:00:00Z",
  last_sent_at: "2026-11-21T08:00:00Z",
  resend_available_at: null,
};
const created = () => json({ ...INVITE, invite_token: TOKEN, invite_link: LINK, sod_warning: false }, 201);
const sodRefusal = () => json({ detail: { code: "sod_confirmation_required", sod_warning: true, message: "sod" } }, 409);

type Handlers = Record<string, (r: Recorded) => Response>;
const base = (extra: Handlers = {}): Handlers => ({
  "GET /api/v1/admin/users": () => json([PLANNER, ME, LEAVER]),
  "GET /api/v1/projects": () => json([project(), project({ id: OTHER_PROJECT.id, code: OTHER_PROJECT.code, name: OTHER_PROJECT.name })]),
  [`GET ${INVITES}`]: () => json([]),
  [`GET /api/v1/admin/projects/${OTHER_PROJECT.id}/invites`]: () => json([]),
  ...extra,
});
const asSystemAdmin = (extra: Handlers = {}) =>
  renderAdmin(PATH, base(extra), { projects: [], systemAdmin: true });
const asProjectAdmin = (extra: Handlers = {}) =>
  renderAdmin(PATH, base(extra), { projects: [ADMIN_PROJECT, OTHER_PROJECT] });

const account = (name: string) => screen.findByRole("listitem", { name }, WAIT);
const button = (name: string, scope: HTMLElement = document.body) =>
  within(scope).getByRole("button", { name });
const emailField = () => screen.getByLabelText(i18n.t("admin.users.inviteEmail"));
const status = (text: string) => screen.findByText(text, {}, WAIT);
const sent = (calls: Recorded[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path);

describe("accounts (System admins)", () => {
  it("lists every account with its status and never offers to deactivate oneself", async () => {
    asSystemAdmin();
    const me = await account(ME.display_name);
    const planner = screen.getByRole("listitem", { name: PLANNER.display_name });
    const leaver = screen.getByRole("listitem", { name: LEAVER.display_name });

    expect(planner).toHaveTextContent(PLANNER.email);
    expect(planner).toHaveTextContent(i18n.t("admin.users.active"));
    expect(planner).toHaveTextContent(i18n.t("admin.users.internal"));
    expect(button(i18n.t("admin.users.deactivate"), planner)).toBeEnabled();
    expect(leaver).toHaveTextContent(i18n.t("admin.users.inactive"));
    expect(button(i18n.t("admin.users.reactivate"), leaver)).toBeEnabled();
    expect(me).toHaveTextContent(i18n.t("admin.users.you"));
    expect(me).toHaveTextContent(i18n.t("admin.users.selfNote"));
    expect(within(me).queryByRole("button")).toBeNull();
  });

  it("creates an account (no tenant anywhere) and re-reads the list", async () => {
    const calls = asSystemAdmin({ "POST /api/v1/admin/users": () => json({ ...PLANNER, id: "00000000-0000-4000-8000-000000000304" }, 201) });
    await account(PLANNER.display_name);
    fireEvent.click(button(i18n.t("admin.users.newAccount")));
    const dialog = screen.getByRole("dialog", { name: i18n.t("admin.users.newTitle") });
    expect(dialog).toHaveTextContent(i18n.t("admin.users.createNote"));
    fireEvent.change(within(dialog).getByLabelText(i18n.t("admin.users.email")), { target: { value: " new@example.test " } });
    fireEvent.change(within(dialog).getByLabelText(i18n.t("admin.users.displayName")), { target: { value: "Synthetic Newcomer" } });
    fireEvent.click(button(i18n.t("admin.users.create"), dialog));

    expect(await status(i18n.t("admin.users.created", { email: "new@example.test" }))).toHaveAttribute("role", "status");
    expect(sent(calls, "POST", "/api/v1/admin/users")[0]?.body).toEqual({ email: "new@example.test", display_name: "Synthetic Newcomer" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(sent(calls, "GET", "/api/v1/admin/users").length).toBeGreaterThan(1);
    }, WAIT);
    expect(JSON.stringify(calls.map((c) => c.body))).not.toContain("tenant");
  });

  it("explains a taken e-mail (409) in the dialog", async () => {
    asSystemAdmin({ "POST /api/v1/admin/users": () => json({ detail: "exists" }, 409) });
    await account(PLANNER.display_name);
    fireEvent.click(button(i18n.t("admin.users.newAccount")));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(i18n.t("admin.users.email")), { target: { value: PLANNER.email } });
    fireEvent.click(button(i18n.t("admin.users.create"), dialog));

    expect(await within(dialog).findByRole("alert", {}, WAIT)).toHaveTextContent(i18n.t("admin.users.duplicate"));
  });

  it("deactivates after a confirmation", async () => {
    const patch = `/api/v1/admin/users/${PLANNER.id}`;
    const calls = asSystemAdmin({ [`PATCH ${patch}`]: () => json({ ...PLANNER, is_active: false }) });
    fireEvent.click(button(i18n.t("admin.users.deactivate"), await account(PLANNER.display_name)));
    const dialog = screen.getByRole("dialog", { name: i18n.t("admin.users.deactivateTitle", { name: PLANNER.display_name }) });
    expect(dialog).toHaveTextContent(i18n.t("admin.users.deactivateBody"));
    expect(sent(calls, "PATCH", patch)).toHaveLength(0);
    fireEvent.click(button(i18n.t("admin.users.deactivate"), dialog));

    expect(await status(i18n.t("admin.users.deactivated", { name: PLANNER.display_name }))).toBeInTheDocument();
    expect(sent(calls, "PATCH", patch)[0]?.body).toEqual({ is_active: false });
  });

  it("says so when the last System admin would be deactivated (409) and re-reads the list", async () => {
    const patch = `/api/v1/admin/users/${PLANNER.id}`;
    const calls = asSystemAdmin({ [`PATCH ${patch}`]: () => json({ detail: "last" }, 409) });
    fireEvent.click(button(i18n.t("admin.users.deactivate"), await account(PLANNER.display_name)));
    fireEvent.click(button(i18n.t("admin.users.deactivate"), screen.getByRole("dialog")));

    expect(await status(i18n.t("admin.users.lastSystemAdmin"))).toBeInTheDocument();
    await waitFor(() => {
      expect(sent(calls, "GET", "/api/v1/admin/users").length).toBeGreaterThan(1);
    }, WAIT);
    expect(screen.getByRole("listitem", { name: PLANNER.display_name })).toHaveTextContent(i18n.t("admin.users.active"));
  });

  it("explains a refused self-deactivation (409 CANNOT_DEACTIVATE_SELF) and re-reads the list", async () => {
    // A stale list or a second tab: the server, not the hidden button, has the last word (T-0085).
    const patch = `/api/v1/admin/users/${PLANNER.id}`;
    const refusal = { detail: "you cannot deactivate your own account", code: "CANNOT_DEACTIVATE_SELF" };
    const calls = asSystemAdmin({ [`PATCH ${patch}`]: () => json(refusal, 409) });
    fireEvent.click(button(i18n.t("admin.users.deactivate"), await account(PLANNER.display_name)));
    fireEvent.click(button(i18n.t("admin.users.deactivate"), screen.getByRole("dialog")));

    expect(await status(i18n.t("admin.users.cannotDeactivateSelf"))).toHaveAttribute("role", "status");
    expect(screen.queryByText(i18n.t("admin.users.lastSystemAdmin"))).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(sent(calls, "GET", "/api/v1/admin/users").length).toBeGreaterThan(1);
    }, WAIT);
  });

  it("reactivates a deactivated account", async () => {
    const patch = `/api/v1/admin/users/${LEAVER.id}`;
    const calls = asSystemAdmin({ [`PATCH ${patch}`]: () => json({ ...LEAVER, is_active: true }) });
    fireEvent.click(button(i18n.t("admin.users.reactivate"), await account(LEAVER.display_name)));

    expect(await status(i18n.t("admin.users.reactivated", { name: LEAVER.display_name }))).toBeInTheDocument();
    expect(sent(calls, "PATCH", patch)[0]?.body).toEqual({ is_active: true });
  });

  it("hides the accounts from a PER-09 who is not a System admin", async () => {
    const calls = asProjectAdmin();
    await screen.findByRole("heading", { name: i18n.t("admin.users.inviteTitle") }, WAIT);
    expect(screen.queryByRole("heading", { name: i18n.t("admin.users.accountsTitle") })).toBeNull();
    expect(sent(calls, "GET", "/api/v1/admin/users")).toHaveLength(0);
  });
});

describe("invites (PER-09, System admins)", () => {
  const projectSelect = () => screen.findByRole("combobox", { name: i18n.t("admin.users.project") }, WAIT);
  const fill = (email: string, persona: RegExp) => {
    fireEvent.change(emailField(), { target: { value: email } });
    fireEvent.click(screen.getByRole("checkbox", { name: persona }));
  };
  const sendButton = () => button(i18n.t("admin.users.send"));

  it("offers only the projects the PER-09 administers", async () => {
    asProjectAdmin();
    const select = await projectSelect();
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      i18n.t("admin.accessRequests.project", { code: ADMIN_PROJECT.code, name: ADMIN_PROJECT.name }),
    ]);
  });

  it("offers every project to a System admin", async () => {
    asSystemAdmin();
    const select = await projectSelect();
    await waitFor(() => {
      expect(within(select).getAllByRole("option")).toHaveLength(2);
    }, WAIT);
  });

  it("invites by e-mail with personas, shows the link once and refreshes the open invitations", async () => {
    let listed: OpenInvite[] = [];
    const calls = asProjectAdmin({
      [`GET ${INVITES}`]: () => json(listed),
      [`POST ${INVITES}`]: () => {
        listed = [INVITE];
        return created();
      },
    });
    await projectSelect();
    expect(screen.getByText(i18n.t("admin.users.inviteNote"))).toBeInTheDocument();
    expect(sendButton()).toBeDisabled();
    fill(" client@example.test ", /PER-15/);
    fireEvent.click(sendButton());

    expect(await screen.findByDisplayValue(LINK, {}, WAIT)).toHaveAttribute("readonly");
    expect(screen.getByLabelText(i18n.t("admin.users.link"))).toHaveValue(LINK);
    expect(screen.getByText(i18n.t("admin.users.sent", { email: INVITE.email }))).toHaveAttribute("role", "status");
    expect(sent(calls, "POST", INVITES)[0]?.body).toEqual({
      email: "client@example.test",
      grants: [{ persona: "PER-15" }],
      confirm_sod: false,
    });
    // The open invitations of that project (T-0053 panel) are re-read and list the new one.
    expect(await screen.findByRole("listitem", { name: INVITE.email }, WAIT)).toHaveTextContent(
      i18n.t("invite.panel.lastSent", { time: formatTime(INVITE.last_sent_at, i18n.language) ?? "" }),
    );
    // The form starts over.
    expect(emailField()).toHaveValue("");

    fireEvent.click(button(i18n.t("invite.panel.close")));
    expect(screen.queryByDisplayValue(LINK)).toBeNull();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("needs the PER-07 site before sending", async () => {
    asProjectAdmin();
    await projectSelect();
    fill("inspector2@example.test", /PER-07/);
    expect(sendButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText(i18n.t("admin.accessRequests.site")), {
      target: { value: "00000000-0000-4000-8000-000000000401" },
    });
    expect(sendButton()).toBeEnabled();
  });

  it("asks for the SoD confirmation before sending PER-09 with hold authority", async () => {
    const calls = asProjectAdmin({
      [`POST ${INVITES}`]: (r) => ((r.body as { confirm_sod: boolean }).confirm_sod ? created() : sodRefusal()),
    });
    await projectSelect();
    fill(INVITE.email, /PER-09/);
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-02/ }));
    fireEvent.click(sendButton());

    const label = i18n.t("admin.accessRequests.project", { code: ADMIN_PROJECT.code, name: ADMIN_PROJECT.name });
    expect(await screen.findByText(i18n.t("admin.users.sodWarning", { project: label }), {}, WAIT)).toHaveAttribute("role", "alert");
    // Any change withdraws the warning.
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-01/ }));
    expect(screen.queryByText(i18n.t("admin.users.sodWarning", { project: label }))).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-01/ }));
    fireEvent.click(sendButton());
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("admin.users.confirmSod") }, WAIT));

    expect(await screen.findByDisplayValue(LINK, {}, WAIT)).toBeInTheDocument();
    const posts = sent(calls, "POST", INVITES).map((c) => (c.body as { confirm_sod: boolean }).confirm_sod);
    expect(posts).toEqual([false, false, true]);
  });

  it("says when inviting is possible again after a 429", async () => {
    asProjectAdmin({
      [`POST ${INVITES}`]: () => new Response(null, { status: 429, headers: { "Retry-After": "60" } }),
    });
    await projectSelect();
    fill(INVITE.email, /PER-15/);
    fireEvent.click(sendButton());

    expect(await screen.findByText(/^Too many invitations to this address\. Inviting is possible again from /, {}, WAIT)).toHaveAttribute("role", "status");
    expect(screen.queryByDisplayValue(LINK)).toBeNull();
  });

  it("asks to try again after a concurrent change (409 INVITE_CONFLICT)", async () => {
    asProjectAdmin({
      [`POST ${INVITES}`]: () => json({ detail: "conflict", code: "INVITE_CONFLICT" }, 409),
    });
    await projectSelect();
    fill(INVITE.email, /PER-15/);
    fireEvent.click(sendButton());

    expect(await status(i18n.t("admin.users.conflict"))).toBeInTheDocument();
  });

  it("lists the open invitations of the chosen project", async () => {
    asSystemAdmin({
      [`GET /api/v1/admin/projects/${OTHER_PROJECT.id}/invites`]: () => json([INVITE]),
    });
    const select = await projectSelect();
    await waitFor(() => {
      expect(within(select).getAllByRole("option")).toHaveLength(2);
    }, WAIT);
    expect(await screen.findByText(i18n.t("invite.panel.empty"), {}, WAIT)).toBeInTheDocument();
    fireEvent.change(select, { target: { value: OTHER_PROJECT.id } });
    expect(await screen.findByRole("listitem", { name: INVITE.email }, WAIT)).toBeInTheDocument();
  });

  it("tells someone who administers nothing so", async () => {
    renderAdmin(PATH, base(), { projects: [OTHER_PROJECT] });
    expect(await screen.findByText(i18n.t("admin.forbidden"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: i18n.t("admin.users.project") })).toBeNull();
  });
});
