// Invite and password screens (T-0053) and the open-invitations panel: WCAG 2.1 AA, contrast
// and 48 px glove targets in both themes on the field breakpoints (diff §6.3). Synthetic data.
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ADMIN_PROJECT, renderAdmin } from "../admin/testing";
import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";
import { INVITE_TOKEN, JOINED, renderAt, SETUP_TOKEN } from "./testing";

const SSO = { password: { available: true }, sso: { provider: "entra", preferred: true, start_url: "/api/v1/auth/sso/entra/start" } };
const PREVIEW = { project_id: JOINED, project_code: "SYN-002", project_name: "Beta Synthetic Tank Farm", inviter_name: "Synthetic Admin" };
const SETUP = { can_set_password: true, project_code: "SYN-002", project_name: "Beta Synthetic Tank Farm" };
const TOO_SHORT = { detail: "short", code: "PASSWORD_TOO_SHORT", min_length: 15, max_length: 256 };
const INVITE = {
  id: "00000000-0000-4000-8000-000000000601",
  email: "client@example.test",
  grants: [{ persona: "PER-15", site_node_id: null }],
  status: "expired",
  expires_at: "2026-11-28T08:00:00Z",
  last_sent_at: "2026-11-21T08:00:00Z",
  resend_available_at: null,
};

async function audit(theme: (typeof THEMES)[number]) {
  const findings = await auditA11y(document.body, theme);
  expect(findings, formatFindings(findings)).toEqual([]);
}

describe.each(THEMES)("invite and password screens in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it("Join project", async () => {
      await setViewport(viewport);
      renderAt(`/invite#${INVITE_TOKEN}`, { "POST /api/v1/admin/invites/preview": () => json(PREVIEW) }, { signedIn: true, theme });
      await screen.findByRole("button", { name: i18n.t("invite.join.submit") });
      await audit(theme);
    });

    it("the invite sign-in with Microsoft, magic link and set-password link", async () => {
      await setViewport(viewport);
      renderAt(`/invite#${INVITE_TOKEN}`, { "GET /api/v1/auth/config": () => json(SSO) }, { theme });
      await screen.findByRole("button", { name: i18n.t("auth.sso.start") });
      await audit(theme);
    });

    it("Create your password with a policy refusal", async () => {
      await setViewport(viewport);
      renderAt(`/invite#${INVITE_TOKEN}.${SETUP_TOKEN}`, {
        "POST /api/v1/admin/invites/setup-preview": () => json(SETUP),
        "POST /api/v1/admin/invites/accept-with-password": () => json(TOO_SHORT, 422),
      }, { theme });
      fireEvent.change(await screen.findByLabelText(i18n.t("password.form.new")), { target: { value: "short" } });
      fireEvent.change(screen.getByLabelText(i18n.t("password.form.confirm")), { target: { value: "short" } });
      fireEvent.click(screen.getByRole("button", { name: i18n.t("invite.setup.submit") }));
      await screen.findByRole("alert");
      await audit(theme);
    });

    it("a refused invite for the wrong user", async () => {
      await setViewport(viewport);
      renderAt(`/invite#${INVITE_TOKEN}`, {
        "POST /api/v1/admin/invites/preview": () => json({ detail: "x", code: "INVITE_WRONG_USER" }, 403),
      }, { signedIn: true, theme });
      await screen.findByRole("button", { name: i18n.t("invite.signOut") });
      await audit(theme);
    });

    it("Send me an e-mail to set my password", async () => {
      await setViewport(viewport);
      renderAt(`/invite#${INVITE_TOKEN}`, {}, { theme });
      fireEvent.click(await screen.findByRole("link", { name: i18n.t("invite.signIn.setPassword") }));
      await screen.findByRole("heading", { name: i18n.t("password.request.setupTitle") });
      await audit(theme);
    });

    it("reset password", async () => {
      await setViewport(viewport);
      renderAt("/reset-password#syntheticResetToken_0123456789-abcdef", {}, { theme });
      await screen.findByLabelText(i18n.t("password.form.new"));
      await audit(theme);
    });

    it("the open-invitations panel with the new link shown", async () => {
      await setViewport(viewport);
      const p = ADMIN_PROJECT.id;
      renderAdmin(`/admin/projects/${p}/members`, {
        [`GET /api/v1/admin/projects/${p}/members`]: () => json([]),
        [`GET /api/v1/admin/projects/${p}/invites`]: () => json([INVITE]),
        [`POST /api/v1/admin/projects/${p}/invites/${INVITE.id}/resend`]: () =>
          json({ ...INVITE, status: "pending", invite_token: "syntheticNewInviteToken_0123456789", invite_link: "http://kerp.test/invite#syntheticNewInviteToken_0123456789" }),
      }, { theme });
      const item = await screen.findByRole("listitem", { name: INVITE.email });
      fireEvent.click(within(item).getByRole("button", { name: i18n.t("invite.panel.resend") }));
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("invite.panel.resend") }));
      await screen.findByRole("button", { name: i18n.t("invite.panel.copy") });
      await audit(theme);
    });
  });
});
