# ADR 0002 — One-engine: port the evaluator-agnostic parts, rewrite the rest (follows TD-002)

- **Status:** accepted
- **Date:** 2026-11-09
- **Follows:** TD-002 (one-engine adopted). Same method as ADR 0001 / TD-001. See
  [ADR 0001](0001-port-inspection-erp-core.md).
- **Affects:** T-0102, T-0103, T-0110, T-0111, T-0112, T-0113, T-0114, T-0115, T-0116,
  T-0117, T-0118, T-0119, T-0122, T-0124, T-0126, T-0127, T-0130

## Context

TD-002 adopted the one-engine model from `PROPOSAL-port-from-aip`: one field-type registry
(`core/fields`) shared by forms, ITP templates and the asset schema; one inspection record
(`oe_inspections_inspection`, `kind IN (inspection, itp, rfi)`); a stateless `eligibility`
service; and a new `inspection_requests` module.

Two codebases hold prior art:

- **Project-AIP** (`kingdonk-ops/Project-AIP`, local clone): the origin of the one-engine
  idea. It has `forms` and `item_types` sharing a field-type catalogue, a Python and a
  TypeScript evaluator proven on shared vector files, revision freezing, and a hash-chained
  sign-off.
- **Inspection-ERP** (`kingdonk-ops/Inspection-ERP`): inspections, ITP, findings,
  calibration/qualification registers, check-sheet forms and a reporting layer.

Both were scanned on 2026-11-09. Three facts dominate the verdicts:

1. **The expression language differs.** KERP mandates **CEL** (`celpy` server, `cel-js`
   client, parity on `tests/contract/conformance_fixtures.json`). AIP chose **JSONLogic**
   on purpose (AIP ADR 0036). Inspection-ERP uses a home-made formula evaluator plus
   Python gate functions. No evaluator, operator lint or expression vector ports as code.
2. **The persistence model differs.** AIP uses SQLAlchemy Core `Table()` objects, scopes by
   tenant only and names tables without the `oe_` prefix. Inspection-ERP uses
   `TenantRecordMixin`, `ie_*` tables and an EAV attribute store. KERP needs
   `BaseRecordMixin`, project-scoped RLS and `custom_fields` JSONB. No table definition
   ports as is.
3. **Some of what KERP needs exists in neither repo.** That covers `inspection_requests`
   (notice, no-show, client delay attribution), WPS/credential/calibration eligibility,
   the KERP lifecycle (`PENDING → … → LOCKED_FOR_TURNOVER`), and Dexie offline sync. AIP's
   `routes_engine/eligibility.py` decides who may approve a step, which is a different
   problem.

## Decision

Port **per component**, by the same rule as ADR 0001: a component is ported only when it
is complete, tested, matches KERP's shape and is faster to adapt than to rewrite.
"Reference" means the implementer reads it for behaviour and edge cases but writes new code.

### Project-AIP (paths relative to the repo root)

| Component | Verdict | Why | Lands in |
|---|---|---|---|
| `packages/contracts/field-types/field-types.json` + `apps/api/aip/modules/item_types/field_types.py`: field-type catalogue, a config schema and a value schema per type, i18n label keys | **Port and adapt** | Evaluator-agnostic and tested. Add `instrument_ref` (calibration-gated) and `calculated` (CEL). Remap or drop `user_ref`/`asset_ref`. Becomes `core/fields`. | T-0110 |
| `item_types/attribute_schema.py`: lints attribute definitions, compiles them to Draft 2020-12 JSON Schema | **Port and adapt** | This is exactly "`oe_asset_field_def` is purely the JSONSchema registry". | T-0102 |
| `item_types/{tables,service,repository}.py`, migration `202610281300_item_types_versions.py`: versioned types, published-version freeze trigger | **Reference** | The versioning model fits "old records validate against their version". The Core tables and tenant-only RLS do not fit. | T-0102 |
| `assets/service.py`: asset pins `item_type_version_id`, validates `attributes` against that pinned version | **Reference** | Same versioning rule. KERP stores the values in `custom_fields`, on `oe_asset_node`/`oe_asset_item`. | T-0102, T-0103 |
| `packages/contracts/unit-vectors/units-v1.json` + `modules/terms/units.py`: store in SI, convert for display, one vector file for Python and TS | **Port and adapt** | Evaluator-agnostic and tested on both runtimes. Extend the unit list (it covers only mm/in, bar/psi, degC/degF). Backs the `measurement` field type. | T-0110 |
| `packages/contracts/forms/form-template.schema.json`: sections, fields, `schemaVersion`, section rules | **Port and adapt** | The structure is evaluator-agnostic. Replace the JSONLogic `$defs/expression` with a CEL string, in the shape of master spec §2.2 (`visible_if`, `validation.cel_expression`). | T-0110 |
| `forms/evaluator.py` + `packages/form-renderer/src/logic/evaluate.ts`: visible / required / invalid / failed per field, complete per section, fail closed on expression error, `EVALUATOR_VERSION` pinned on each revision | **Reference** | The semantics are what KERP wants. Every expression call goes through JSONLogic, so rewrite both over `celpy`/`cel-js`. Keep the fail-closed rule and the pinned evaluator version. | T-0110 |
| `approvals/guards/jsonlogic.py`, `form-renderer/src/logic/jsonlogic.ts` | **Do not port** | JSONLogic. | — |
| `forms/lint.py`: ordered checks, stable codes (`DUPLICATE_KEY`, `RULE_READS_HIDDEN_FIELD`, …), error/warning split | **Port and adapt** | The structural checks carry over. Rewrite the operator and field-reference checks using the CEL parser's AST. | T-0110, T-0113 |
| `forms/service.py` `_canonical_hash` + `form_revisions_freeze()` trigger (migration `202610261200_forms_templates_revisions.py`) | **Port and adapt** | `schema_hash` = sha256 of canonical JSON. The BEFORE UPDATE OR DELETE trigger blocks changes to a non-draft revision. This is how a pinned `template_revision_id` stays honest. Rename to `oe_forms_*`. | T-0110, T-0113 |
| `packages/contracts/jsonlogic-vectors/forms/` + `forms/tests/test_contract_parity.py` + `evaluate.test.ts`: parity harness | **Port the shape, rewrite the content** | Keep the file shape (`{version, description, cases:[{name, schema, answers, mode, context, expected}]}`), the frozen-version rule and the `version == EVALUATOR_VERSION` assertion for `tests/contract/conformance_fixtures.json`. `expressions-v1` is pure JSONLogic. The `evaluate-*` cases are kept after their `visibleIf`/`requiredIf` are converted to CEL. | T-0110 |
| `inspections/responses.py`: append-only answers linked by `supersedes_id`, current-answer view, idempotent on `client_id` under a row lock, validated against the pinned revision | **Port and adapt** | Fits KERP's immutable-answer and offline-idempotency rules. Move onto `BaseRecordMixin` + project RLS. | T-0111, T-0126 |
| `inspections/signoff_chain.py`: `genesis_hash`, `entry_hash` (RFC 8785 + sha256), `content_hash`, `verify_chain`, all pure functions | **Port and adapt** | Small, pure and tested. Add the provider seam (pyHanko later), plus geolocation and the UTC timestamp on the entry for `INSPECTED_PASSED`. | T-0119 |
| `inspections/{tables,service,repository}.py` | **Reference** | It has `kind` and pins `template_revision_id`. But it anchors only to an asset, and its state runs through AIP's approvals workflow instead of KERP's fixed lifecycle. There is no `run_checks`. | T-0111 |
| `inspections/itp.py`, `itp_rules.py`: hold/witness/review/surveillance points, `notice_hours`/`ready_at`, waive a witness after the notice lapses, `BLOCKED_BY_HOLD` | **Reference** | Bound to AIP approvals presets. Read it for the notice and waiver rules. | T-0113, T-0114, T-0115 |
| `inspections/workflow.py`, `acknowledgements.py`, `approvals/routes_engine/eligibility.py` | **Do not port** | Coupled to AIP's approvals engine. The eligibility file decides who may approve a step, not whether a credential is valid. | — |
| `docs/blueprint/modules/eligibility/README.md`: green/amber/red computed at query time, hard block, audited override, credential snapshot on sign-off | **Reference** | Design notes only. There is no code behind them. | T-0111 |
| `packages/form-renderer/src/{FormRenderer,FormSection,ErrorSummary}.tsx`, `fields/*.tsx`, `logic/types.ts` | **Port and adapt** | Evaluator-agnostic React components that already use `t()`. Swap in the CEL logic layer and add TanStack Query + Dexie wiring. | T-0110 (types), T-0130 |

### Inspection-ERP (paths relative to `backend/` or `frontend/`)

| Component | Verdict | Why | Lands in |
|---|---|---|---|
| `app/modules/registers/service.py`: `is_equipment_calibrated`, `is_person_qualified`, `expiring_*` | **Port and adapt (logic only)** | This is the expiry check that eligibility needs. Two fixes: an empty expiry date must **fail closed** for calibration-gated tools (today it counts as valid forever), and match equipment by reference id, not free-text tag. | T-0112, T-0118, T-0111 (eligibility) |
| `app/core/reporting/{model,pdf,html,excel}.py`: one report model rendered to HTML, PDF (reportlab) or Excel, with injection-safe branding (`safe_color`, `safe_image_uri`) | **Port and adapt** | Pure Python and tested. Fits the worker plus lazy-import rule once reportlab (and openpyxl) are added to the heavy-lib lint (TD-038). No spec doc names reportlab; this ADR records the choice. Basis for inspection and sign-off certificates and the inspection-request notice PDF. | T-0119, T-0115 |
| `app/core/forms/evaluation.py` + `tests/test_forms.py` (formula, visibility, versioning, project override) | **Port the tests, not the code** | The evaluator is replaced by CEL. Its test cases become CEL conformance fixtures. | T-0110 |
| `app/core/validation/gates.py`: required_responses, photos_present, supervisor_signoff (verifier ≠ inspector), inspector_qualified, equipment_calibrated, procedure_approved | **Reference** | Python gates, and a core→module import. Use the list as the starter catalogue of CEL rule sets. | T-0111, T-0122, T-0124 |
| `app/modules/inspections/*`: server-stamped `sign_off()`, server-derived `required_response_keys`, read-only once terminal, `TransitionLog` | **Reference** | The lifecycle and schema don't match. Rewrite the patterns, don't copy them. | T-0111, T-0119 |
| `app/modules/itp/*`: `POINT_TYPES` hold/witness/review/surveillance, progress percentage | **Reference** | Too thin: hold points block nothing. | T-0113, T-0114 |
| `app/modules/findings/*`: verifier ≠ resolver, drawing-pin and geo fields | **Reference** | KERP splits findings into Cat A punch and NCR. | T-0116, T-0117 |
| `app/modules/{workforce,scheduler,equipment,iso_workspace}`, `core/workflow/engine.py` | **Do not port** | Out of scope, or no notice/attribution model. | — |
| `frontend/src/modules/studio/FormBuilder.tsx`, `frontend/src/modules/{inspections,itp,findings,equipment}/` | **Reference** | No Dexie, EAV-era types, old lifecycle. The AIP renderer is the better base. | T-0113, T-0130 |

### Written from the spec (no usable source)

- **`inspection_requests`** (T-0115): the state machine, minimum notice, no-show → client
  delay attribution. Only AIP's ITP witness-notice rule is useful prior art.
- **`eligibility`** (T-0111): a stateless service over WPS, credentials and calibration,
  built on the ported expiry logic above.
- **The KERP inspection lifecycle and `run_checks` wiring** (T-0111).
- **Offline outbox** (T-0126/T-0127): AIP's `apps/field` is an empty shell.

## Consequences

- Each affected task's notes name its source files, so the implementer ports from them
  instead of starting cold. Neither repo is vendored. Ported code must pass KERP's gates
  (`mypy --strict`, ruff `ANN`, 90% coverage, i18n `t()`) and keeps a one-line docstring
  credit (`Adapted from Project-AIP …` / `Adapted from Inspection-ERP …`).
- **No JSONLogic enters KERP.** A reviewer rejects any `json-logic` dependency or
  JSONLogic-shaped expression node.
- `schema_hash` plus the revision freeze trigger are the mechanism behind "pins
  `template_revision_id`" and "old records validate against their version". They are
  in T-0110/T-0113 scope, not extras.
- The proposal's other AIP features did not survive spec consolidation: write-back,
  type inheritance, migration preview, publish test-fill, section library, report-slot
  mapping, `auto_create_itp`, competency-aware assignment and programmes. They exist only
  in the archived proposal, and most are unbuilt in AIP too. TD-039 asks whether to bring
  any back. Until it is answered, they are out of scope.
