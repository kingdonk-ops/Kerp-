import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  cellText,
  effectiveHiddenColumns,
  filterRows,
  toCsv,
  type RegisterColumn,
  type SavedView,
  type SavedViewStore,
} from "./model";

export type DownloadFn = (filename: string, content: string, mimeType: string) => void;

/** Browser download of a generated file (BOM so spreadsheet apps read UTF-8). */
export const browserDownload: DownloadFn = (filename, content, mimeType) => {
  const url = URL.createObjectURL(new Blob(["﻿", content], { type: mimeType }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
};

export interface RegisterPageProps<Row> {
  /** Already translated by the caller. */
  title: string;
  columns: readonly RegisterColumn<Row>[];
  /** The loaded rows; filtering and export work on these (server-side paging: TD-182). */
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  loading?: boolean;
  onRowClick?: (row: Row) => void;
  /** Saved views UI is shown only when a store is given. */
  savedViews?: SavedViewStore;
  /** CSV file name without extension. */
  exportFileName?: string;
  download?: DownloadFn;
}

// Glove targets (diff §6.3): every control is at least `--spacing-touch` (48 px) high and wide.
const CONTROL = "min-h-touch min-w-touch rounded border border-line bg-panel px-2";
const BUTTON = "min-h-touch min-w-touch rounded border border-line bg-panel px-3 disabled:opacity-50";
const CHECKBOX = "size-touch shrink-0 accent-brand";

/**
 * Shared list screen: quick + column filters, column visibility, CSV export, saved views.
 * When the saved-view store's identity (`SavedViewStore.key`: user + register) changes, the
 * register state is remounted so views, filters and the selection never leak across users or
 * registers. Without a store, reuse for another register needs a React `key` from the caller.
 */
export function RegisterPage<Row>(props: RegisterPageProps<Row>) {
  return <RegisterView<Row> key={props.savedViews?.key ?? ""} {...props} />;
}

function RegisterView<Row>({
  title,
  columns,
  rows,
  rowKey,
  loading = false,
  onRowClick,
  savedViews,
  exportFileName = "export",
  download = browserDownload,
}: RegisterPageProps<Row>) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const [hiddenState, setHidden] = useState<ReadonlySet<string>>(
    () => new Set(columns.filter((column) => column.defaultHidden === true).map((column) => column.id)),
  );
  const [views, setViews] = useState<SavedView[]>(() => savedViews?.list() ?? []);
  const [selectedView, setSelectedView] = useState("");
  const [viewName, setViewName] = useState("");

  // Whatever was stored or defaulted, at least one column stays visible (no empty table).
  const hidden = useMemo(() => effectiveHiddenColumns(columns, hiddenState), [columns, hiddenState]);
  const visibleColumns = useMemo(() => columns.filter((column) => !hidden.has(column.id)), [columns, hidden]);
  const shown = useMemo(
    () => filterRows(rows, visibleColumns, { query, columnFilters }),
    [rows, visibleColumns, query, columnFilters],
  );

  const toggleColumn = (id: string) => {
    // From the effective set, so a column re-shown by the guard toggles as displayed.
    const next = new Set(hidden);
    if (!next.delete(id)) next.add(id);
    setHidden(next);
  };

  const applyView = (id: string) => {
    setSelectedView(id);
    const view = views.find((item) => item.id === id);
    if (view === undefined) return;
    setQuery(view.query);
    setColumnFilters({ ...view.columnFilters });
    setHidden(new Set(view.hiddenColumns));
    setViewName(view.name);
  };

  const saveView = () => {
    if (savedViews === undefined) return;
    const view = savedViews.save({
      name: viewName.trim(),
      query,
      columnFilters,
      hiddenColumns: columns.filter((column) => hidden.has(column.id)).map((column) => column.id),
    });
    setViews(savedViews.list());
    setSelectedView(view.id);
  };

  const deleteView = () => {
    if (savedViews === undefined || selectedView === "") return;
    savedViews.remove(selectedView);
    setViews(savedViews.list());
    setSelectedView("");
  };

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{title}</h1>

      <div className="flex flex-wrap items-end gap-touch-gap">
        <label className="flex flex-col text-sm">
          {t("shells.register.quickFilter")}
          <input className={CONTROL} type="search" value={query} onChange={(e) => { setQuery(e.target.value); }} />
        </label>

        <fieldset className="flex flex-wrap gap-touch-gap text-sm">
          <legend className="text-sm">{t("shells.register.columns")}</legend>
          {columns.map((column) => {
            const isLastVisible = !hidden.has(column.id) && visibleColumns.length === 1;
            return (
              <label key={column.id} className="flex min-h-touch items-center gap-1 pr-2">
                <input
                  type="checkbox"
                  className={CHECKBOX}
                  checked={!hidden.has(column.id)}
                  disabled={isLastVisible}
                  onChange={() => { toggleColumn(column.id); }}
                />
                {column.header}
              </label>
            );
          })}
        </fieldset>

        <button
          type="button"
          className={BUTTON}
          onClick={() => { download(`${exportFileName}.csv`, toCsv(shown, visibleColumns), "text/csv;charset=utf-8"); }}
        >
          {t("shells.register.exportCsv")}
        </button>

        {savedViews !== undefined && (
          <div className="flex flex-wrap items-end gap-touch-gap text-sm">
            <label className="flex flex-col">
              {t("shells.register.savedViews.label")}
              <select className={CONTROL} value={selectedView} onChange={(e) => { applyView(e.target.value); }}>
                <option value="">{t("shells.register.savedViews.none")}</option>
                {views.map((view) => (
                  <option key={view.id} value={view.id}>
                    {view.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col">
              {t("shells.register.savedViews.name")}
              <input className={CONTROL} value={viewName} onChange={(e) => { setViewName(e.target.value); }} />
            </label>
            <button type="button" className={BUTTON} disabled={viewName.trim() === ""} onClick={saveView}>
              {t("shells.register.savedViews.save")}
            </button>
            <button type="button" className={BUTTON} disabled={selectedView === ""} onClick={deleteView}>
              {t("shells.register.savedViews.delete")}
            </button>
          </div>
        )}
      </div>

      <p className="text-sm text-muted" aria-live="polite">
        {t("shells.register.count", { shown: shown.length, total: rows.length })}
      </p>

      <div className="overflow-x-auto rounded border border-line bg-panel">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              {visibleColumns.map((column) => (
                <th key={column.id} data-column={column.id} scope="col" className="px-3 py-2 font-semibold">
                  {column.header}
                </th>
              ))}
            </tr>
            <tr>
              {visibleColumns.map((column) => (
                <td key={column.id} className="px-3 pb-2">
                  <input
                    className={`${CONTROL} w-full`}
                    aria-label={t("shells.register.columnFilter", { column: column.header })}
                    value={columnFilters[column.id] ?? ""}
                    onChange={(e) => { setColumnFilters((current) => ({ ...current, [column.id]: e.target.value })); }}
                  />
                </td>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={Math.max(1, visibleColumns.length)} className="px-3 py-4 text-muted">
                  {t("app.loading")}
                </td>
              </tr>
            ) : shown.length === 0 ? (
              <tr>
                <td colSpan={Math.max(1, visibleColumns.length)} className="px-3 py-4 text-muted">
                  {t("shells.register.empty")}
                </td>
              </tr>
            ) : (
              shown.map((row) => (
                <tr
                  key={rowKey(row)}
                  className={onRowClick ? "cursor-pointer border-t border-line hover:bg-surface" : "border-t border-line"}
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={onRowClick ? () => { onRowClick(row); } : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(row); } : undefined}
                >
                  {visibleColumns.map((column) => (
                    <td key={column.id} className={onRowClick ? "h-touch px-3 py-2" : "px-3 py-2"}>
                      {cellText(column.value(row))}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
