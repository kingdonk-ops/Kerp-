import { describe, expect, it } from "vitest";

import { memoryStorage } from "../../auth/tokenStore";
import { filterRows, localSavedViewStore, savedViewsStorageKey, toCsv, type RegisterColumn } from "./model";

interface Delivery {
  id: string;
  ref: string;
  supplier: string;
  qty: number;
  note: string | null;
}

const COLUMNS: RegisterColumn<Delivery>[] = [
  { id: "ref", header: "Reference", value: (row) => row.ref },
  { id: "supplier", header: "Supplier", value: (row) => row.supplier },
  { id: "qty", header: "Quantity", value: (row) => row.qty },
  { id: "note", header: "Note", value: (row) => row.note },
];

const ROWS: Delivery[] = [
  { id: "1", ref: "DN-0001", supplier: "Synthetic Steel Co", qty: 12, note: null },
  { id: "2", ref: "DN-0002", supplier: "Example Insulation Ltd", qty: 4, note: "Short shipment" },
  { id: "3", ref: "DN-0003", supplier: "Synthetic Steel Co", qty: 7, note: "=HYPERLINK(\"x\")" },
];

describe("filterRows", () => {
  it("returns every row without filters", () => {
    expect(filterRows(ROWS, COLUMNS, { query: "", columnFilters: {} })).toEqual(ROWS);
  });

  it("matches the quick filter case-insensitively across the searchable columns", () => {
    expect(filterRows(ROWS, COLUMNS, { query: "steel", columnFilters: {} }).map((r) => r.id)).toEqual(["1", "3"]);
    expect(filterRows(ROWS, COLUMNS, { query: "short", columnFilters: {} }).map((r) => r.id)).toEqual(["2"]);
    // Only the columns passed in are searched (hidden columns are excluded by the caller).
    expect(filterRows(ROWS, COLUMNS.slice(0, 2), { query: "short", columnFilters: {} })).toEqual([]);
  });

  it("combines per-column filters with the quick filter (AND)", () => {
    const state = { query: "synthetic", columnFilters: { qty: "7", ref: "" } };
    expect(filterRows(ROWS, COLUMNS, state).map((r) => r.id)).toEqual(["3"]);
  });

  it("ignores filters on unknown columns", () => {
    expect(filterRows(ROWS, COLUMNS, { query: "", columnFilters: { gone: "x" } })).toHaveLength(3);
  });
});

describe("toCsv", () => {
  it("writes a header and one CRLF-separated line per row", () => {
    const csv = toCsv(ROWS.slice(0, 1), COLUMNS);
    expect(csv).toBe("Reference,Supplier,Quantity,Note\r\nDN-0001,Synthetic Steel Co,12,\r\n");
  });

  it("quotes commas, quotes and newlines (RFC 4180)", () => {
    const columns: RegisterColumn<{ v: string }>[] = [{ id: "v", header: "Value, raw", value: (r) => r.v }];
    expect(toCsv([{ v: 'a "b"\nc' }], columns)).toBe('"Value, raw"\r\n"a ""b""\nc"\r\n');
  });

  it("neutralises spreadsheet formulas in text cells but not in numbers", () => {
    const columns: RegisterColumn<{ s: string; n: number }>[] = [
      { id: "s", header: "S", value: (r) => r.s },
      { id: "n", header: "N", value: (r) => r.n },
    ];
    const csv = toCsv(
      [
        { s: "=1+1", n: -3 },
        { s: "@SUM(A1)", n: 0 },
        { s: "+x", n: 1 },
        { s: "-y", n: 2 },
      ],
      columns,
    );
    expect(csv.split("\r\n").slice(1, 5)).toEqual(["'=1+1,-3", "'@SUM(A1),0", "'+x,1", "'-y,2"]);
  });

  it("neutralises cells starting with tab or carriage return (quoting the CR cell)", () => {
    const columns: RegisterColumn<{ s: string }>[] = [{ id: "s", header: "S", value: (r) => r.s }];
    expect(toCsv([{ s: "\t=1" }], columns)).toBe("S\r\n'\t=1\r\n");
    expect(toCsv([{ s: "\r=1" }], columns)).toBe("S\r\n\"'\r=1\"\r\n");
  });

  it("renders booleans and empty values", () => {
    const columns: RegisterColumn<{ b: boolean | undefined }>[] = [{ id: "b", header: "B", value: (r) => r.b }];
    expect(toCsv([{ b: true }, { b: undefined }], columns)).toBe("B\r\ntrue\r\n\r\n");
  });
});

describe("localSavedViewStore", () => {
  it("saves, lists, replaces by name and removes views per user and register", () => {
    const storage = memoryStorage();
    const store = localSavedViewStore(storage, "user-1", "deliveries");
    expect(store.key).toBe(savedViewsStorageKey("user-1", "deliveries"));

    expect(store.list()).toEqual([]);
    const first = store.save({ name: "Steel", query: "steel", hiddenColumns: ["note"], columnFilters: {} });
    expect(store.list()).toEqual([first]);

    const replaced = store.save({ name: "Steel", query: "co", hiddenColumns: [], columnFilters: { qty: "7" } });
    expect(replaced.id).toBe(first.id);
    expect(store.list()).toEqual([replaced]);

    const other = localSavedViewStore(storage, "user-2", "deliveries");
    expect(other.list()).toEqual([]);

    store.remove(first.id);
    expect(store.list()).toEqual([]);
    expect(storage.getItem(savedViewsStorageKey("user-1", "deliveries"))).toBe("[]");
  });

  it("treats corrupt or foreign stored data as no views", () => {
    const storage = memoryStorage();
    storage.setItem(savedViewsStorageKey("u", "r"), "{not json");
    expect(localSavedViewStore(storage, "u", "r").list()).toEqual([]);
    storage.setItem(savedViewsStorageKey("u", "r"), JSON.stringify([{ id: 1 }, "x", null]));
    expect(localSavedViewStore(storage, "u", "r").list()).toEqual([]);
  });
});
