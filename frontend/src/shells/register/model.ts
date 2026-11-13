// RegisterPage logic without React: filtering, CSV export and the local saved-view store.
import type { KeyValueStorage } from "../../auth/tokenStore";

export type CellValue = string | number | boolean | null | undefined;

export interface RegisterColumn<Row> {
  id: string;
  /** Already translated by the caller. */
  header: string;
  value: (row: Row) => CellValue;
  defaultHidden?: boolean;
}

export interface FilterState {
  query: string;
  /** Column id -> case-insensitive "contains" text. */
  columnFilters: Readonly<Record<string, string>>;
}

export interface SavedView extends FilterState {
  id: string;
  name: string;
  hiddenColumns: readonly string[];
}

export type SavedViewDraft = Omit<SavedView, "id">;

/** Where a register's saved views live. Local for now; server-side views are TD-179. */
export interface SavedViewStore {
  /** Stable identity (user + register); stores with equal keys hold the same views. */
  readonly key: string;
  list(): SavedView[];
  /** Creates the view, or replaces the existing view with the same name. */
  save(draft: SavedViewDraft): SavedView;
  remove(id: string): void;
}

export function cellText(value: CellValue): string {
  return value === null || value === undefined ? "" : String(value);
}

/** Rows matching the quick filter (any of `columns`) and every per-column filter. */
export function filterRows<Row>(
  rows: readonly Row[],
  columns: readonly RegisterColumn<Row>[],
  state: FilterState,
): Row[] {
  const query = state.query.trim().toLowerCase();
  const perColumn = Object.entries(state.columnFilters)
    .map(([id, text]) => [columns.find((column) => column.id === id), text.trim().toLowerCase()] as const)
    .filter((entry): entry is readonly [RegisterColumn<Row>, string] => entry[0] !== undefined && entry[1] !== "");

  return rows.filter((row) => {
    const matchesQuery =
      query === "" || columns.some((column) => cellText(column.value(row)).toLowerCase().includes(query));
    return matchesQuery && perColumn.every(([column, text]) => cellText(column.value(row)).toLowerCase().includes(text));
  });
}

const FORMULA_START = /^[=+\-@\t\r]/;

function csvCell(value: CellValue): string {
  let text = cellText(value);
  // Spreadsheet formula injection: text cells only, numbers keep their sign.
  if (typeof value === "string" && FORMULA_START.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** RFC 4180 CSV (CRLF line endings, header row) of `rows` over `columns`. */
export function toCsv<Row>(rows: readonly Row[], columns: readonly RegisterColumn<Row>[]): string {
  const lines = [
    columns.map((column) => csvCell(column.header)),
    ...rows.map((row) => columns.map((column) => csvCell(column.value(row)))),
  ];
  return lines.map((cells) => `${cells.join(",")}\r\n`).join("");
}

export function savedViewsStorageKey(userId: string, registerId: string): string {
  return `kerp.views.${userId}.${registerId}`;
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every((item) => typeof item === "string")
  );
}

function isSavedView(value: unknown): value is SavedView {
  if (typeof value !== "object" || value === null) return false;
  const view = value as Record<string, unknown>;
  return (
    typeof view.id === "string" &&
    typeof view.name === "string" &&
    typeof view.query === "string" &&
    Array.isArray(view.hiddenColumns) &&
    view.hiddenColumns.every((item) => typeof item === "string") &&
    isStringRecord(view.columnFilters)
  );
}

/** Saved views in the preferences storage, per user and register. */
export function localSavedViewStore(storage: KeyValueStorage, userId: string, registerId: string): SavedViewStore {
  const key = savedViewsStorageKey(userId, registerId);

  const read = (): SavedView[] => {
    try {
      const parsed: unknown = JSON.parse(storage.getItem(key) ?? "[]");
      return Array.isArray(parsed) ? parsed.filter(isSavedView) : [];
    } catch {
      return [];
    }
  };
  const write = (views: SavedView[]) => storage.setItem(key, JSON.stringify(views));

  return {
    key,
    list: read,
    save(draft) {
      const views = read();
      const existing = views.find((view) => view.name === draft.name);
      const view: SavedView = { ...draft, id: existing?.id ?? crypto.randomUUID() };
      write(existing ? views.map((item) => (item.id === existing.id ? view : item)) : [...views, view]);
      return view;
    },
    remove(id) {
      write(read().filter((view) => view.id !== id));
    },
  };
}
