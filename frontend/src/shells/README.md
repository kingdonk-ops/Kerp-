# `src/shells` — reusable domain shells (T-0025)

Consolidation §6: module pages are built on these three shells, never from scratch.
Import from `src/shells` (`index.ts`). Callers pass already-translated titles, headers and
tab labels; the shells translate their own chrome through `t()` (`shells.*` keys).

## RegisterPage (`register/`)

```tsx
<RegisterPage<Delivery>
  title={t("logistics.deliveries.title")}
  columns={[{ id: "ref", header: t("..."), value: (row) => row.ref }]}
  rows={data ?? []}
  loading={isPending}
  rowKey={(row) => row.id}
  onRowClick={(row) => navigate(row.id)}
  savedViews={localSavedViewStore(services.preferences, user.id, "deliveries")}
  exportFileName="deliveries"
/>
```

- **Filter**: quick filter across the visible columns + per-column "contains" filters (AND),
  client-side over the loaded rows (server-side filter/sort/paging: TD-182).
- **Columns**: visibility toggles; `defaultHidden` columns start hidden; the last visible
  column cannot be hidden. At least one column always shows: if a stored view (or
  `defaultHidden` on every column) would hide them all, the first column is shown.
- **Export**: CSV (RFC 4180, CRLF, UTF-8 BOM) of the filtered rows and visible columns.
  Text cells starting with `= + - @` or tab/CR are prefixed with `'` (formula injection).
  PDF and full-result-set export: TD-180. `download` is injectable for tests.
- **Saved views**: shown only when a `SavedViewStore` is passed. `localSavedViewStore`
  keeps them in the preferences storage under `kerp.views.<userId>.<registerId>` (each part
  URI-encoded, `.` as `%2E`, so ids containing `.` cannot collide); saving
  under an existing name replaces it. Server-side views: TD-179.
- **Store identity**: `SavedViewStore.key` identifies user + register. When it changes (another
  sign-in, the same mounted page reused for another register) the register state — views,
  selection, filters, columns — is remounted, so nothing leaks across. A store recreated
  inline with the same key keeps the state. Without a store, give the page a React `key`
  when reusing it for another register.

## DetailPage (`detail/`)

```tsx
<DetailPage
  title={pack.code}
  gate={{ state: "blocked", label: t("..."), reasons: failedChecks }}
  tabs={[{ id: "overview", label: t("..."), render: () => <Overview /> }]}
  audit={{ key: ["audit", "test_pack", pack.id], load: () => fetchAudit(pack.id) }}
  actions={<ReleaseButton />}
/>
```

- **Gate banner** (`GateBanner`): `open | passed | hold | blocked` + label + reasons (e.g.
  failed `run_checks` results). `hold`/`blocked` use `role="alert"`, the others `role="status"`.
- **Tabs**: WAI-ARIA tabs, routed through the `?tab=<id>` search parameter (other params are
  kept, history entry replaced); unknown ids fall back to the first tab; Left/Right arrows move
  (wrapping), Home/End jump to the first/last tab; roving `tabindex`; only the selected tab
  carries `aria-controls` (only its panel is rendered).
- **Audit log**: an `audit` source adds an "Audit log" tab (id `audit`, reserved: a module tab
  with that id logs a dev-time `console.error`, and is dropped when an `audit` source is given),
  loaded with TanStack Query when first opened; AI-initiated entries (`source: "ai"`) are
  badged. No audit/gate endpoint exists yet — pages pass the data (TD-181).

## Dynamic Form Renderer (`form/`)

`DynamicForm` renders a form template (EDGE-SCREENS §2.2 schema, `form/types.ts`);
`evaluateForm` is the pure engine behind it.

- **Data types**: `text`, `number` (JSON number = CEL double), `boolean`, `select`, `date`.
  Shared field-type registry / dual-runtime parity: T-0110 (TD-177).
- **CEL** through the `RuleEvaluator` seam, default `src/lib/cel` `evaluate`. Context:
  `{ ...context, fields }` — `fields` holds only visible, non-empty values and cannot be
  overridden by `context`.
- **Fail closed**: a rule passes only on `ok && value === true`.
  - `visible_if` error / non-bool → hidden. Hidden values are dropped (sanitised) and never
    submitted or validated. Visibility is iterated to a fixpoint so fields depending on a
    hidden field hide too; a field whose visibility never settles is hidden.
  - `required_if` → required unless it evaluates to exactly `false`. `evaluation.required`
    holds the visible fields required now (static or active `required_if`); it drives the
    asterisk and `aria-required`. An empty field makes
    `fields.x` a `NO_SUCH_KEY`, so guard optional references with `has(fields.x)`.
  - `validation` → `invalid` (template `error_message`) on `false`; `rule_error` (generic
    message) on an error or non-bool. Empty optional fields skip validation.
- **Robustness**: a malformed value or `context` (e.g. a bad `{"$int": ...}`) makes the CEL
  runtime throw; each rule evaluation catches that as `INVALID_ARGUMENT`, which fails closed
  like any other rule error. Values and errors use own-property reads only, so field keys
  such as `constructor` or `__proto__` behave like any other key.
- **Errors** show once a field is touched or after a submit attempt; `onSubmit` receives the
  sanitised values only when the form is valid.
- Template labels / messages are authored content, shown as-is (localisation: TD-183).
  Calculated fields and range rules: TD-178.
