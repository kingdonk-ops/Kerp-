// Lobby screens (T-0041) with the request form open and every request status shown: WCAG 2.1
// AA, contrast and 48 px glove targets in both themes on the field breakpoints (diff §6.3).
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderAdmin } from "../admin/testing";
import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { FAKE_USER, json } from "../test/fakeApi";
import { applyTheme, THEMES } from "../theme/theme";

const SLOW = 20_000;
const TANK = { id: "00000000-0000-4000-8000-000000000711", code: "SYN-TANK", name: "Synthetic Tank Farm", member: false };
const JETTY = { id: "00000000-0000-4000-8000-000000000712", code: "SYN-JETTY", name: "Synthetic Jetty", member: true };
const BASE = {
  project_id: TANK.id,
  project_code: TANK.code,
  project_name: TANK.name,
  message: null,
  created_at: "2026-11-19T08:00:00Z",
  decided_at: null,
  withdrawn_at: null,
  rejection_reason: null,
  request_again_at: null,
};
const MINE = [
  { ...BASE, id: "00000000-0000-4000-8000-000000000801", status: "pending" },
  { ...BASE, id: "00000000-0000-4000-8000-000000000802", project_code: "SYN-B", status: "approved" },
  {
    ...BASE,
    id: "00000000-0000-4000-8000-000000000803",
    project_code: "SYN-C",
    status: "rejected",
    rejection_reason: "Crew is full.",
    request_again_at: "2099-01-01T08:00:00Z",
  },
  { ...BASE, id: "00000000-0000-4000-8000-000000000804", project_code: "SYN-D", status: "withdrawn" },
];

describe.each(THEMES)("lobby in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it("lobby list, code result with the request form, and Active requests", async () => {
      await setViewport(viewport);
      renderAdmin(
        "/lobby",
        {
          "GET /api/v1/auth/me": () => json({ ...FAKE_USER, is_internal: true }),
          "GET /api/v1/lobby/projects": () => json([TANK, JETTY]),
          "GET /api/v1/lobby/projects/by-code/SYN-TANK": () =>
            json({ ...TANK, lookup_ticket: "synthetic", lookup_ticket_expires_at: "2099-01-01T08:00:00Z" }),
          "GET /api/v1/access-requests/mine": () => json(MINE),
        },
        { projects: [], theme },
      );
      fireEvent.change(await screen.findByLabelText(i18n.t("lobby.code.label")), {
        target: { value: "SYN-TANK" },
      });
      fireEvent.click(screen.getByRole("button", { name: i18n.t("lobby.code.find") }));
      const code = await screen.findByRole("region", { name: i18n.t("lobby.code.title") });
      fireEvent.click(
        await within(code).findByRole("button", { name: i18n.t("lobby.requestAccess") }),
      );
      // axe measures a text field's contrast only with a value in it.
      fireEvent.change(within(code).getByLabelText(i18n.t("lobby.message")), {
        target: { value: "Joining the shutdown crew." },
      });
      await screen.findByText("Synthetic Jetty", { exact: false });
      await screen.findByText(i18n.t("lobby.requests.reason", { reason: "Crew is full." }));

      const findings = await auditA11y(document.body, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    }, SLOW);
  });
});
