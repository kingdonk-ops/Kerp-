// Navigation item mapping of diff §6.1 (T-0042).
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { NavItemList } from "./Layout";
import { NAV_GROUP_IDS, NAV_ITEMS, registeredNavItems, type NavItem } from "./nav";

describe("navigation items (diff §6.1)", () => {
  it("maps every item of the §6.1 table to its group", () => {
    const byGroup = Object.fromEntries(
      NAV_GROUP_IDS.map((g) => [g, NAV_ITEMS.filter((i) => i.group === g).map((i) => i.id)]),
    );
    expect(byGroup).toEqual({
      overview: ["dashboards", "myWork"],
      backbone: ["projects", "assetHierarchy", "items", "teams"],
      execution: ["inspections", "itps", "fieldTime", "progress", "lookAhead"],
      logistics: ["materials", "equipment", "storeman"],
      documents: ["documents", "transmittals", "cdrl", "drawings"],
      defenseClaims: ["tqs", "variations", "nodEot", "claims"],
      controlsInsights: ["p6Schedule", "reporting", "moneyMirror"],
      admin: ["users", "personas", "system", "accessRequests"],
    });
    for (const entry of NAV_ITEMS) {
      expect(i18n.exists(`nav.items.${entry.id}`), entry.id).toBe(true);
    }
  });

  it("shows only items whose screens exist", () => {
    const items: NavItem[] = [
      { group: "execution", id: "inspections", path: "/execution/inspections" },
      { group: "execution", id: "itps" },
      { group: "logistics", id: "materials", path: "/logistics/materials" },
    ];
    expect(registeredNavItems("execution", items).map((i) => i.id)).toEqual(["inspections"]);
    expect(registeredNavItems("admin", items)).toEqual([]);

    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <NavItemList group="execution" items={items} />
        </MemoryRouter>
      </I18nextProvider>,
    );
    const links = within(screen.getByRole("list")).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent(i18n.t("nav.items.inspections"));
    expect(links[0]).toHaveAttribute("href", "/execution/inspections");
  });

  it("registers only the built screens: Projects for everyone, Admin system and access requests for project admins", () => {
    expect(NAV_GROUP_IDS.flatMap((g) => registeredNavItems(g)).map((i) => i.path)).toEqual([
      "/backbone/projects",
    ]);
    const admins = new Set(["projectAdmin"] as const);
    expect(
      NAV_GROUP_IDS.flatMap((g) => registeredNavItems(g, NAV_ITEMS, admins)).map((i) => i.path),
    ).toEqual(["/backbone/projects", "/admin/system/devices", "/admin/access-requests"]);
  });
});
