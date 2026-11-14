// Glove targets (48 px) and contrast of the shells, both themes, on the field breakpoints
// (diff §6.3, T-0046). Runs in Chrome (Vitest browser mode): real layout and Tailwind CSS.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { memoryStorage } from "../auth/tokenStore";
import { i18n } from "../i18n";
import { auditA11y, FIELD_VIEWPORTS, formatFindings, setViewport } from "../test/a11y";
import { applyTheme, THEMES } from "../theme/theme";
import { DetailPage, type AuditEntry } from "./detail/DetailPage";
import { GateBanner } from "./detail/GateBanner";
import { DynamicForm } from "./form/DynamicForm";
import type { FormTemplate } from "./form/types";
import { localSavedViewStore, type RegisterColumn } from "./register/model";
import { RegisterPage } from "./register/RegisterPage";

interface Tag {
  id: string;
  tag: string;
  area: string;
  status: string;
}

// Synthetic fixtures only.
const COLUMNS: RegisterColumn<Tag>[] = [
  { id: "tag", header: "Tag", value: (row) => row.tag },
  { id: "area", header: "Area", value: (row) => row.area },
  { id: "status", header: "Status", value: (row) => row.status },
];

const ROWS: Tag[] = [
  { id: "a", tag: "SYN-PIPE-001", area: "Area 10", status: "OPEN" },
  { id: "b", tag: "SYN-PIPE-002", area: "Area 20", status: "CLOSED" },
];

const AUDIT: AuditEntry[] = [
  { id: "e1", at: "2026-03-01T08:30:00Z", actor: "Synthetic Inspector", action: "status_changed", source: "user" },
  { id: "e2", at: "2026-03-02T09:00:00Z", actor: "Assistant", action: "field_updated", source: "ai" },
];

const TEMPLATE: FormTemplate = {
  fields: [
    {
      field_key: "visual_result",
      label: "Visual result",
      help: "Synthetic help text.",
      data_type: "select",
      required: true,
      options: [
        { value: "ACCEPT", label: "Accept" },
        { value: "REJECT", label: "Reject" },
      ],
    },
    { field_key: "defect_length_mm", label: "Defect length (mm)", data_type: "number", required: true },
    { field_key: "remarks", label: "Remarks", data_type: "text" },
    { field_key: "witnessed", label: "Witnessed", data_type: "boolean" },
    { field_key: "inspected_on", label: "Inspected on", data_type: "date" },
  ],
};

function withProviders(ui: ReactNode, initialEntry = "/records/1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <main>{ui}</main>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SHELLS: { name: string; mount: () => Promise<HTMLElement> }[] = [
  {
    name: "RegisterPage",
    mount: () => {
      const store = localSavedViewStore(memoryStorage(), "u-synthetic", "tags");
      store.save({ name: "Open only", query: "OPEN", columnFilters: {}, hiddenColumns: [] });
      const { container } = withProviders(
        <RegisterPage<Tag>
          title="Synthetic tags"
          columns={COLUMNS}
          rows={ROWS}
          rowKey={(row) => row.id}
          onRowClick={() => undefined}
          savedViews={store}
          download={() => undefined}
        />,
      );
      return Promise.resolve(container);
    },
  },
  {
    name: "DetailPage (gate banners, tabs, audit log)",
    mount: async () => {
      const { container } = withProviders(
        <>
          <DetailPage
            title="SYN-TP-001"
            subtitle="Synthetic test pack"
            gate={{ state: "blocked", label: "Release", reasons: ["Synthetic check failed"] }}
            tabs={[{ id: "overview", label: "Overview", render: () => <p>Overview body</p> }]}
            audit={{ key: ["audit", "a11y"], load: () => Promise.resolve(AUDIT) }}
            actions={
              <button type="button" className="min-h-touch rounded bg-brand px-4 text-on-brand">
                Release
              </button>
            }
          />
          {(["open", "passed", "hold"] as const).map((state) => (
            <GateBanner key={state} gate={{ state, label: "Synthetic gate", reasons: ["Synthetic reason"] }} />
          ))}
        </>,
        "/records/1?tab=audit",
      );
      await screen.findByText("Synthetic Inspector");
      return container;
    },
  },
  {
    name: "DynamicForm (with validation errors)",
    mount: () => {
      const { container } = withProviders(<DynamicForm template={TEMPLATE} onSubmit={() => undefined} />);
      // A submit attempt shows the required errors, so error text is checked too.
      fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.form.submit") }));
      return Promise.resolve(container);
    },
  },
];

describe.each(THEMES)("shells in the %s theme", (theme) => {
  afterEach(() => {
    applyTheme("standard");
  });

  describe.each(FIELD_VIEWPORTS)("at $name", (viewport) => {
    it.each(SHELLS)("$name meets WCAG 2.1 AA and 48 px targets", async ({ mount }) => {
      await setViewport(viewport);
      applyTheme(theme);
      const root = await mount();
      const findings = await auditA11y(root, theme);
      expect(findings, formatFindings(findings)).toEqual([]);
    });
  });
});
