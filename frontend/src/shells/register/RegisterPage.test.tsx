import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { memoryStorage } from "../../auth/tokenStore";
import { i18n } from "../../i18n";
import { localSavedViewStore, type RegisterColumn } from "./model";
import { RegisterPage } from "./RegisterPage";

interface Tag {
  id: string;
  tag: string;
  area: string;
  status: string;
}

const COLUMNS: RegisterColumn<Tag>[] = [
  { id: "tag", header: "Tag", value: (row) => row.tag },
  { id: "area", header: "Area", value: (row) => row.area },
  { id: "status", header: "Status", value: (row) => row.status, defaultHidden: true },
];

const ROWS: Tag[] = [
  { id: "a", tag: "SYN-PIPE-001", area: "Area 10", status: "OPEN" },
  { id: "b", tag: "SYN-PIPE-002", area: "Area 20", status: "CLOSED" },
  { id: "c", tag: "SYN-VLV-003", area: "Area 10", status: "OPEN" },
];

function renderRegister(overrides: Partial<Parameters<typeof RegisterPage<Tag>>[0]> = {}) {
  const download = vi.fn();
  const utils = render(
    <RegisterPage<Tag>
      title="Synthetic tags"
      columns={COLUMNS}
      rows={ROWS}
      rowKey={(row) => row.id}
      exportFileName="tags"
      download={download}
      {...overrides}
    />,
  );
  return { ...utils, download };
}

function bodyRows() {
  const [, body] = screen.getAllByRole("rowgroup");
  return within(body!).queryAllByRole("row");
}

function columnHeaders() {
  return screen.getAllByRole("columnheader").map((cell) => cell.getAttribute("data-column"));
}

describe("RegisterPage", () => {
  it("renders the title, visible columns and one row per record", () => {
    renderRegister();
    expect(screen.getByRole("heading", { level: 1, name: "Synthetic tags" })).toBeInTheDocument();
    expect(columnHeaders()).toEqual(["tag", "area"]);
    expect(bodyRows()).toHaveLength(3);
    expect(screen.getByText(i18n.t("shells.register.count", { shown: 3, total: 3 }))).toBeInTheDocument();
  });

  it("filters with the quick filter and per-column filters", () => {
    renderRegister();
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "pipe" } });
    expect(bodyRows()).toHaveLength(2);

    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.columnFilter", { column: "Area" })), {
      target: { value: "20" },
    });
    expect(bodyRows()).toHaveLength(1);
    expect(screen.getByText("SYN-PIPE-002")).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches and a loading state", () => {
    const { rerender } = renderRegister();
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "zzz" } });
    expect(screen.getByText(i18n.t("shells.register.empty"))).toBeInTheDocument();

    rerender(
      <RegisterPage<Tag> title="Synthetic tags" columns={COLUMNS} rows={[]} rowKey={(row) => row.id} loading />,
    );
    expect(screen.getByText(i18n.t("app.loading"))).toBeInTheDocument();
  });

  it("toggles column visibility", () => {
    renderRegister();
    fireEvent.click(screen.getByRole("checkbox", { name: "Status" }));
    expect(columnHeaders()).toEqual(["tag", "area", "status"]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Tag" }));
    expect(columnHeaders()).toEqual(["area", "status"]);
  });

  it("never hides the last visible column", () => {
    renderRegister({ columns: COLUMNS.slice(0, 1) });
    expect(screen.getByRole("checkbox", { name: "Tag" })).toBeDisabled();
  });

  it("exports the filtered rows and visible columns as CSV", () => {
    const { download } = renderRegister();
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "vlv" } });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.register.exportCsv") }));

    expect(download).toHaveBeenCalledWith("tags.csv", "Tag,Area\r\nSYN-VLV-003,Area 10\r\n", "text/csv;charset=utf-8");
  });

  it("calls onRowClick with the record", () => {
    const onRowClick = vi.fn();
    renderRegister({ onRowClick });
    fireEvent.click(screen.getByText("SYN-PIPE-002"));
    expect(onRowClick).toHaveBeenCalledWith(ROWS[1]);

    fireEvent.keyDown(screen.getByText("SYN-VLV-003").closest("tr")!, { key: "Enter" });
    expect(onRowClick).toHaveBeenLastCalledWith(ROWS[2]);
  });

  it("hides saved views when no store is provided", () => {
    renderRegister();
    expect(screen.queryByLabelText(i18n.t("shells.register.savedViews.label"))).not.toBeInTheDocument();
  });

  it("saves, applies and deletes saved views", () => {
    const store = localSavedViewStore(memoryStorage(), "user-1", "tags");
    renderRegister({ savedViews: store });

    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "area 10" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Status" }));
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.savedViews.name")), {
      target: { value: "Area 10 incl. status" },
    });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.register.savedViews.save") }));
    expect(store.list()).toMatchObject([
      { name: "Area 10 incl. status", query: "area 10", hiddenColumns: [], columnFilters: {} },
    ]);

    // Reset the state, then re-apply the view.
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Status" }));
    expect(bodyRows()).toHaveLength(3);

    const picker = screen.getByLabelText(i18n.t("shells.register.savedViews.label"));
    fireEvent.change(picker, { target: { value: store.list()[0]!.id } });
    expect(screen.getByLabelText(i18n.t("shells.register.quickFilter"))).toHaveValue("area 10");
    expect(columnHeaders()).toEqual(["tag", "area", "status"]);
    expect(bodyRows()).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.register.savedViews.delete") }));
    expect(store.list()).toEqual([]);
    expect(within(picker).getAllByRole("option")).toHaveLength(1);
  });

  it.each([
    ["user", "user-2", "tags"],
    ["register", "user-1", "valves"],
  ])("drops views, selection and filters when the %s changes", (_what, nextUser, nextRegister) => {
    const storage = memoryStorage();
    const first = localSavedViewStore(storage, "user-1", "tags");
    first.save({ name: "U1 secret view", query: "vlv", hiddenColumns: [], columnFilters: {} });
    const props = { title: "Synthetic tags", columns: COLUMNS, rows: ROWS, rowKey: (row: Tag) => row.id };
    const { rerender } = render(<RegisterPage<Tag> {...props} savedViews={first} />);

    const picker = () => screen.getByLabelText(i18n.t("shells.register.savedViews.label"));
    fireEvent.change(picker(), { target: { value: first.list()[0]!.id } });
    expect(bodyRows()).toHaveLength(1);

    const second = localSavedViewStore(storage, nextUser, nextRegister);
    rerender(<RegisterPage<Tag> {...props} savedViews={second} />);

    expect(within(picker()).getAllByRole("option")).toHaveLength(1);
    expect(screen.queryByText("U1 secret view")).not.toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t("shells.register.quickFilter"))).toHaveValue("");
    expect(screen.getByLabelText(i18n.t("shells.register.savedViews.name"))).toHaveValue("");
    expect(bodyRows()).toHaveLength(3);

    // Saving now goes to the new store only.
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.savedViews.name")), { target: { value: "Mine" } });
    fireEvent.click(screen.getByRole("button", { name: i18n.t("shells.register.savedViews.save") }));
    expect(second.list().map((view) => view.name)).toEqual(["Mine"]);
    expect(first.list().map((view) => view.name)).toEqual(["U1 secret view"]);
  });

  it("keeps state when an equal store is recreated on rerender", () => {
    const storage = memoryStorage();
    const props = { title: "Synthetic tags", columns: COLUMNS, rows: ROWS, rowKey: (row: Tag) => row.id };
    const { rerender } = render(<RegisterPage<Tag> {...props} savedViews={localSavedViewStore(storage, "u", "tags")} />);
    fireEvent.change(screen.getByLabelText(i18n.t("shells.register.quickFilter")), { target: { value: "pipe" } });
    rerender(<RegisterPage<Tag> {...props} savedViews={localSavedViewStore(storage, "u", "tags")} />);
    expect(screen.getByLabelText(i18n.t("shells.register.quickFilter"))).toHaveValue("pipe");
  });

  it("does not save a view without a name", () => {
    const store = localSavedViewStore(memoryStorage(), "user-1", "tags");
    renderRegister({ savedViews: store });
    expect(screen.getByRole("button", { name: i18n.t("shells.register.savedViews.save") })).toBeDisabled();
  });
});
