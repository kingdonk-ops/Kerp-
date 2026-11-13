import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../../i18n";
import { DetailPage, type AuditEntry, type DetailPageProps } from "./DetailPage";
import { GateBanner } from "./GateBanner";

const TABS: DetailPageProps["tabs"] = [
  { id: "overview", label: "Overview", render: () => <p>Overview body</p> },
  { id: "items", label: "Items", render: () => <p>Items body</p> },
];

// Synthetic audit trail.
const AUDIT: AuditEntry[] = [
  {
    id: "e1",
    at: "2026-03-01T08:30:00Z",
    actor: "Synthetic Inspector",
    action: "status_changed",
    summary: "OPEN → HOLD",
    source: "user",
  },
  { id: "e2", at: "2026-03-02T09:00:00Z", actor: "Assistant", action: "field_updated", source: "ai" },
];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

function renderDetail(props: Partial<DetailPageProps> = {}, initialEntry = "/records/1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        {children}
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(<DetailPage title="SYN-TP-001" tabs={TABS} {...props} />, { wrapper });
}

describe("DetailPage tabs", () => {
  it("renders the title and opens the first tab by default", () => {
    renderDetail({ subtitle: "Synthetic test pack" });
    expect(screen.getByRole("heading", { level: 1, name: "SYN-TP-001" })).toBeInTheDocument();
    expect(screen.getByText("Synthetic test pack")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Overview body");
  });

  it("routes tabs through the ?tab= search parameter", () => {
    renderDetail({}, "/records/1?tab=items&keep=1");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Items body");

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Overview body");
    expect(screen.getByTestId("location")).toHaveTextContent("?tab=overview&keep=1");
  });

  it("falls back to the first tab for an unknown tab id", () => {
    renderDetail({}, "/records/1?tab=nope");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Overview body");
  });

  it("moves between tabs with the arrow keys", () => {
    renderDetail();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Items" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Items" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Items" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Items" })).toHaveAttribute("aria-selected", "true");
  });

  it("renders header actions", () => {
    renderDetail({ actions: <button type="button">Release</button> });
    expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument();
  });
});

describe("DetailPage audit tab", () => {
  it("is only present when an audit source is given", () => {
    renderDetail();
    expect(screen.queryByRole("tab", { name: i18n.t("shells.detail.audit.tab") })).not.toBeInTheDocument();
  });

  it("loads the audit log lazily and lists entries with actor, action and AI source", async () => {
    const load = vi.fn(() => Promise.resolve(AUDIT));
    renderDetail({ audit: { key: ["audit", "tp", "1"], load } });
    expect(load).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: i18n.t("shells.detail.audit.tab") }));
    const list = await screen.findByRole("list", { name: i18n.t("shells.detail.audit.tab") });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Synthetic Inspector");
    expect(items[0]).toHaveTextContent("status_changed");
    expect(items[0]).toHaveTextContent("OPEN → HOLD");
    expect(within(items[0]!).getByText(/2026/)).toHaveAttribute("datetime", "2026-03-01T08:30:00Z");
    expect(items[1]).toHaveTextContent(i18n.t("shells.detail.audit.source.ai"));
    expect(items[0]).not.toHaveTextContent(i18n.t("shells.detail.audit.source.ai"));
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("shows an empty state", async () => {
    renderDetail({ audit: { key: ["audit", "empty"], load: () => Promise.resolve([]) } }, "/r?tab=audit");
    expect(await screen.findByText(i18n.t("shells.detail.audit.empty"))).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(AUDIT);
    renderDetail({ audit: { key: ["audit", "err"], load } }, "/r?tab=audit");
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("app.retry") }));
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));
  });
});

describe("GateBanner", () => {
  it.each([
    ["open", "status"],
    ["passed", "status"],
    ["hold", "alert"],
    ["blocked", "alert"],
  ] as const)("renders the %s state with role %s", (state, role) => {
    render(<GateBanner gate={{ state, label: "Hydrotest release" }} />);
    const banner = screen.getByRole(role);
    expect(banner).toHaveAttribute("data-gate-state", state);
    expect(banner).toHaveTextContent(i18n.t(`shells.detail.gate.states.${state}`));
    expect(banner).toHaveTextContent("Hydrotest release");
  });

  it("lists the reasons that block the gate", () => {
    render(
      <GateBanner
        gate={{ state: "blocked", label: "Fill & pressurize", reasons: ["2 open Cat A punches", "1 uninspected weld"] }}
      />,
    );
    const reasons = within(screen.getByRole("alert")).getAllByRole("listitem");
    expect(reasons.map((item) => item.textContent)).toEqual(["2 open Cat A punches", "1 uninspected weld"]);
  });

  it("is shown at the top of the DetailPage when a gate is given", () => {
    renderDetail({ gate: { state: "hold", label: "Coating hold point" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Coating hold point");
  });
});
