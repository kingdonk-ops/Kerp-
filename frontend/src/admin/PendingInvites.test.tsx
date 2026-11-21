// Open invitations on the project members screen: list, Resend (confirmation, new link shown
// once, 429), Revoke (T-0053, diff §4.1 Re-send). Synthetic data only.
import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { formatTime } from "./ui";
import type { OpenInvite } from "./invites";
import { ADMIN_PROJECT, renderAdmin } from "./testing";

const P = ADMIN_PROJECT.id;
const PATH = `/admin/projects/${P}/members`;
const LIST = `GET /api/v1/admin/projects/${P}/invites`;
const ID = "00000000-0000-4000-8000-000000000601";
const RESEND = `POST /api/v1/admin/projects/${P}/invites/${ID}/resend`;
const REVOKE = `DELETE /api/v1/admin/projects/${P}/invites/${ID}`;
const LINK = "http://kerp.test/invite#syntheticNewInviteToken_0123456789";
const INVITE: OpenInvite = {
  id: ID,
  email: "client@example.test",
  grants: [{ persona: "PER-15", site_node_id: null }],
  status: "pending",
  expires_at: "2026-11-28T08:00:00Z",
  last_sent_at: "2026-11-21T08:00:00Z",
  resend_available_at: null,
};
const sent = () => json({ ...INVITE, invite_token: "syntheticNewInviteToken_0123456789", invite_link: LINK });
const t = (iso: string) => formatTime(iso, i18n.language) ?? "";
const row = async () => screen.findByRole("listitem", { name: INVITE.email });
const dialog = () => screen.getByRole("dialog");
const button = (scope: HTMLElement, key: "invite.panel.resend" | "invite.panel.revoke") =>
  within(scope).getByRole("button", { name: i18n.t(key) });

function open(handlers: Record<string, (r: Recorded) => Response>, invite: OpenInvite = INVITE) {
  return renderAdmin(PATH, {
    [`GET /api/v1/admin/projects/${P}/members`]: () => json([]),
    [LIST]: () => json([invite]),
    ...handlers,
  });
}

describe("open invitations panel", () => {
  it("lists e-mail, persona, expiry and last sent; an expired invite can be re-sent", async () => {
    open({}, { ...INVITE, status: "expired" });
    const item = await row();

    expect(item).toHaveTextContent(`${INVITE.email} · ${i18n.t("invite.panel.expired")}`);
    expect(item).toHaveTextContent("PER-15");
    expect(item).toHaveTextContent(i18n.t("invite.panel.expires", { time: t(INVITE.expires_at) }));
    expect(item).toHaveTextContent(i18n.t("invite.panel.lastSent", { time: t(INVITE.last_sent_at) }));
    expect(button(item, "invite.panel.resend")).toBeEnabled();
  });

  it("disables Resend until resend_available_at", async () => {
    const later = new Date(Date.now() + 3_600_000).toISOString();
    open({}, { ...INVITE, resend_available_at: later });
    const item = await row();

    expect(button(item, "invite.panel.resend")).toBeDisabled();
    expect(item).toHaveTextContent(i18n.t("invite.panel.resendFrom", { time: t(later) }));
  });

  it("re-sends after a confirmation and shows the new link once", async () => {
    const calls = open({ [RESEND]: sent });
    fireEvent.click(button(await row(), "invite.panel.resend"));

    expect(dialog()).toHaveTextContent(i18n.t("invite.panel.resendBody"));
    expect(calls.some((c) => c.path.endsWith("/resend"))).toBe(false);
    fireEvent.click(button(dialog(), "invite.panel.resend"));

    expect(await screen.findByDisplayValue(LINK)).toHaveAttribute("readonly");
    expect(screen.getByText(i18n.t("invite.panel.resent", { email: INVITE.email }))).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByRole("button", { name: i18n.t("invite.panel.close") }));
    expect(screen.queryByDisplayValue(LINK)).toBeNull();
    expect(document.body.textContent).not.toContain("syntheticNewInviteToken");
  });

  it("says when re-sending is possible again after a 429", async () => {
    open({ [RESEND]: () => new Response(null, { status: 429, headers: { "Retry-After": "60" } }) });
    fireEvent.click(button(await row(), "invite.panel.resend"));
    const before = Date.now();
    fireEvent.click(button(dialog(), "invite.panel.resend"));

    const status = await screen.findByText(/Re-sending is possible again from/);
    const possible = [0, 1, 2].map((s) => t(new Date(before + (60 + s) * 1000).toISOString()));
    expect(possible.some((time) => status.textContent === i18n.t("invite.panel.throttled", { time }))).toBe(true);
    expect(screen.queryByDisplayValue(LINK)).toBeNull();
  });

  it("revokes after a confirmation; an accepted or revoked invite is explained and refreshed", async () => {
    let listed = 0;
    const calls = open({
      [LIST]: () => json(++listed === 1 ? [INVITE] : []),
      [REVOKE]: () => json({ detail: "gone", code: "INVITE_NOT_RESENDABLE" }, 409),
    });
    fireEvent.click(button(await row(), "invite.panel.revoke"));
    fireEvent.click(button(dialog(), "invite.panel.revoke"));

    expect(await screen.findByText(i18n.t("invite.panel.gone"))).toBeInTheDocument();
    expect(await screen.findByText(i18n.t("invite.panel.empty"))).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });
});
