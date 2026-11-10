# Module detail — Change of scope register

Module `scope_changes` (epic **E-04-14**). Captures field findings that the planned scope is
wrong — work already done, item not found, condition worse than scoped, steel removed — and
manages the conversation with the client until the scope is agreed and updated. It sits
**before** commercial variations (E-06-03): a scope change may or may not become a variation.

Reference example: a change-of-scope tracker with columns Site | Asset | RSW | Current scope |
Comments / change | Client response | Contractor response; typical comments "job looks done
already — descope?", "not found on revisit — descope?", "2 off PFC corroded — scope to
increase", "propose full assessment", "client to confirm brace removed", "walk off with client".
Per ADR 0003 that layout is the default register view, not a fixed format. Examples synthetic.

Precedence: module detail (3).

---

## 1. Record

`oe_scope_change` (`BaseRecordMixin`, project RLS):

| Field | Notes |
|---|---|
| `ref` | numbering pattern, e.g. `{project}-SC-{seq:4}` |
| `anchor_node_id` / `anchor_item_id` | usually the work anchor (RSW); any node or item allowed; `anchor_role` recorded. Multi-anchor via link table. |
| `current_scope_snapshot` | copy of the anchor's scope at raise time: activity lines (code, description, qty, UOM, budget hrs) from the node's custom fields / FEP data |
| `change_type` | value list (pack-editable): `descope`, `scope_increase`, `scope_change`, `already_done`, `not_found`, `needs_assessment`, `client_to_confirm` |
| `observation` | rich text — what was found |
| `proposed_lines` | proposed new scope: activity code, description, qty, UOM, hours delta per resource group |
| `photos`, attachments | uploads T-0016; offline capture |
| `raised_by`, `raised_at`, `source` | field / office / client / `ai` (AI-raised needs confirmation) |
| `walkdown_required`, `walkdown_at`, `walkdown_attendees` | joint inspection ("walk off with client") |
| `status` | §2 |
| `responses` | thread of party responses (§3) |
| `outcome` | agreed lines, agreed by, date |
| `variation_ref` | link to a variation (E-06-03) when there is commercial impact |
| `linked` | TQ / RFI / plan / NCR links |

## 2. Lifecycle

```
draft → submitted → under_review ⇄ walkdown_scheduled → agreed → implemented
                                    ↘ rejected
any pre-agreed → withdrawn
```

- `submitted` makes it visible to the client in the portal (layouts as in
  [`portal-plan-review.md`](portal-plan-review.md) §2; comments §5 reused).
- `agreed` requires the agreed lines and the client's acceptance (portal vote or a recorded
  client response by an authorised user, with evidence attached).
- `implemented` applies the agreed lines to the anchor's scope through `asset_management`
  service **as a new revision** of the scope fields (old values kept in history, ADR 0003
  additive rule); budget qty/hours roll up; open plans that include the anchor are flagged
  "scope changed since issue". Rule set `scope_change.implement` (ADR 0004).

## 3. Responses

The tracker's two response columns become a threaded `oe_scope_change_response`: `party`
(`client`, `contractor`, other configured parties), `author`, `body`, `at`, `proposed_status`.
The register shows the latest response per party as columns, so the familiar layout is kept.

## 4. Views and reports

- Register view (default columns as the reference tracker) with filters by site, asset, change
  type, status, party awaiting response; saved views ([`register-views.md`](register-views.md)).
- Per-RSW panel on the asset detail page: open scope changes and their status.
- Export to XLSX in the tracker layout; included in weekly progress reporting.
- Portal: client sees submitted changes for their scope and can respond / accept.

## 5. Events

`scope_change.submitted`, `scope_change.responded`, `scope_change.agreed`,
`scope_change.implemented`, `scope_change.rejected`.

## 6. Suggested breakdown

1. Record, lifecycle, responses, snapshot of current scope (M).
2. Implement: scope revision through asset service + plan flagging (S).
3. Register view defaults, XLSX export, portal exposure (S).
4. Variation link (after E-06-03) (S).
