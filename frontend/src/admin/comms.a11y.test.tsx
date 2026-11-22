// Admin → Communications (T-0229) with dialogs, preview and refusals open, and the public
// /unsubscribe page: WCAG 2.1 AA, contrast and 48 px glove targets in both themes on the field
// breakpoints (diff §6.3). Synthetic data only.
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { renderAt } from "../invite/testing";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";
import { BROADCAST, BROADCAST_ID, COUNTS, MAIL_NOT_READY, PREVIEW, SEND, STATS, TEMPLATES } from "./commsFixtures";
import { renderAdmin } from "./testing";

const WAIT = { timeout: 5000 };
const SLOW = 20_000;
const SYS = { systemAdmin: true, projects: [] };

async function audit(theme: (typeof THEMES)[number]) {
  const findings = await auditA11y(document.body, theme);
  expect(findings, formatFindings(findings)).toEqual([]);
}

describe.each(THEMES)("communications screens in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it("broadcast composer with the confirm dialog and an opened delivery", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/communications/broadcasts",
        {
          "GET /api/v1/notifications/broadcasts": () => json([BROADCAST]),
          [`GET /api/v1/notifications/broadcasts/${BROADCAST_ID}`]: () => json({ ...BROADCAST, delivery: COUNTS }),
          "GET /api/v1/projects": () => json([]),
          "POST /api/v1/notifications/broadcasts/preview": () => json({ recipient_count: 12 }),
        },
        { ...SYS, theme },
      );
      fireEvent.change(await screen.findByLabelText(i18n.t("admin.comms.broadcasts.subject"), {}, WAIT), {
        target: { value: "Synthetic notice" },
      });
      fireEvent.change(screen.getByLabelText(i18n.t("admin.comms.broadcasts.body")), { target: { value: "Body." } });
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.comms.broadcasts.count") }));
      await screen.findByText(i18n.t("admin.comms.broadcasts.recipients", { count: 12 }), {}, WAIT);
      fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.comms.broadcasts.send") }));
      await screen.findByRole("dialog", {}, WAIT);
      fireEvent.click(await screen.findByRole("button", { name: i18n.t("admin.comms.broadcasts.showDelivery") }, WAIT));
      await screen.findByText("91.7%", {}, WAIT);
      await audit(theme);
    }, SLOW);

    it("delivery stats and history with an invalid address", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/communications/delivery",
        {
          "GET /api/v1/mail/stats": () => json(STATS),
          "GET /api/v1/mail/sends": () => json([SEND]),
          "GET /api/v1/mail/templates": () => json(TEMPLATES),
        },
        { ...SYS, theme },
      );
      await screen.findByText(SEND.recipient_masked, {}, WAIT);
      fireEvent.change(screen.getByLabelText(i18n.t("admin.comms.delivery.address")), { target: { value: "nope" } });
      await audit(theme);
    }, SLOW);

    it("template editor with the health banner, a refusal, the preview and the reset dialog", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/admin/communications/templates",
        {
          "GET /api/v1/mail/templates": () => json(TEMPLATES),
          "GET /api/v1/settings/deployment-config": () => json(MAIL_NOT_READY),
          "GET /api/v1/mail/templates/auth.magic_link/en-AU/revisions": () => json([]),
          "POST /api/v1/mail/templates/auth.magic_link/en-AU/preview": () => json(PREVIEW),
        },
        { ...SYS, theme },
      );
      fireEvent.click(
        await screen.findByRole("button", { name: i18n.t("admin.comms.templates.open", { key: "auth.magic_link" }) }, WAIT),
      );
      const editor = within(
        await screen.findByRole("region", { name: i18n.t("admin.comms.templates.editorTitle", { key: "auth.magic_link" }) }, WAIT),
      );
      fireEvent.click(editor.getByRole("button", { name: i18n.t("admin.comms.templates.preview") }));
      await editor.findByRole("region", { name: i18n.t("admin.comms.templates.previewTitle") }, WAIT);
      fireEvent.change(editor.getByLabelText(i18n.t("admin.comms.templates.fields.subject")), { target: { value: "" } });
      fireEvent.click(editor.getByRole("button", { name: i18n.t("admin.comms.templates.save") }));
      await editor.findByText(i18n.t("admin.comms.templates.problems.empty"), {}, WAIT);
      fireEvent.click(editor.getByRole("button", { name: i18n.t("admin.comms.templates.reset") }));
      await screen.findByRole("dialog", {}, WAIT);
      await screen.findByText(i18n.t("admin.comms.templates.health.title"), {}, WAIT);
      await audit(theme);
    }, SLOW);

    it("the unsubscribe page and its confirmation", async () => {
      await setViewport(viewport);
      renderAt("/unsubscribe#syntheticOptOutToken_0123456789-abcdefABCDEF", {
        "POST /api/v1/notifications/broadcasts/unsubscribe": () => json({ status: "ok" }),
      }, { theme });
      await audit(theme);
      fireEvent.click(await screen.findByRole("button", { name: i18n.t("unsubscribe.confirm") }));
      await screen.findByRole("status", {}, WAIT);
      await audit(theme);
    }, SLOW);
  });
});
