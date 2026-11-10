# Coastal project inputs — what each file is and where it lands

Reviewed 2026-11-10. The user supplied ~45 files from a structural-integrity remediation
contract and from the CUI inspection department, "for ideas, inspiration or specs" for modules
already specified and still to be specified. Per ADR 0003 they are **examples, not formats**.
Every source file contains real client and personal data: none may be committed or used as a
fixture (CLAUDE.md §6, TD-069). People appear here by role only and identifiers as shapes.

Detail notes:
- [`coastal-inputs-qa-documents.md`](coastal-inputs-qa-documents.md) — ITP, inspection
  notifications, ITRs/FICs, client RSW reports, technical query, CDRL, approved vs original
  ITR templates (incl. the ITP page layout the template engine must reproduce, §1).
- [`coastal-inputs-registers-and-cui.md`](coastal-inputs-registers-and-cui.md) — welder
  qualification register, inspector certificate, training matrix, welding and NDT workbooks,
  and the CUI department's inspection-system exports (53-row concept mapping to KERP).

## 1. File → module map

| File (family) | What it is | Lands in |
|---|---|---|
| Front-end planning workbook | Site / Asset / RSW / Activity scope with budget qty, UOM, hours, scaffold hrs, shutdown flag, validation and client-response columns; summary of hours Online vs Shutdown by asset | ADR 0003 levels + field defs; [`register-views.md`](../spec/modules/register-views.md) (any column, filters, saved/grouped views) — E-01-01; plan lines — E-04-12 |
| Shut resource allocation & forecast | one-week shut: scope areas × crews × day × AM/PM heads, roster capacity per crew | [`planner-and-work-plans.md`](../spec/modules/planner-and-work-plans.md) §7 allocation grid — E-04-12 |
| P6 schedule (XER) + baseline PDF + basis of schedule | WBS Project → Site → Online/Shutdown → Asset; activity names `Asset / Activity / RSW`; Shutdown/Online/On-hold activity codes; one calendar per shut window; trade resources (Scaffolder, Painter/Blaster, Mechanical, Civil) with hours; `SCA Erect` / `SCA Dism` activities | P6 import spec (existing); planner §3–5 and ADR 0006 (overlay / interrupt plans); scaffolding §5 |
| Change of scope tracker | Site / Asset / RSW / current scope / comment / client + contractor responses | [`change-of-scope.md`](../spec/modules/change-of-scope.md) — E-04-14 |
| Communication matrix | communication formats (daily, weekly, monthly, shutdown planning per shut, TQ, MDR, transmittal…) × people with To/Cc and frequency | [`portal-plan-review.md`](../spec/modules/portal-plan-review.md) §4 distribution matrix — E-07-04 |
| Signed ITP | the target ITP style (cover sheet, landscape body, per-party codes) | QA notes §1; T-0113, T-0119; TODOs below |
| Inspection notifications ("RFIs") ×5 | daily per-discipline call-out with up to 4 slots and results | QA notes §2; T-0115 (TD-045) |
| Completed reports/FICs, client RSW report, scope sheet | weld QA report, bolt tensioning, coating report, client RSW defect report | QA notes §3–6; T-0203, T-0111, E-05-09 |
| Technical query register | TQ forms with impact flags and client response | QA notes §7; E-05-04 |
| CDRL requirements | client deliverables list with codes, due rules, review codes | QA notes §8 (proposed CDRL register); T-0120, T-0121, E-05-01/03/05 |
| ITR templates (approved new vs original) | concrete pour, pedestal repair, structural, UTT, coating, welding, bolt tensioning, grout, grating | QA notes §9 (diff = what the client demanded); T-0113 default pack |
| Welder qualification register, inspector certificate, training matrix | competency data | registers notes §1–3; T-0118, T-0202..T-0207 |
| Welding report template & register, NDT request & register | weld register, NDT requests to provider, sampling | registers notes §4–5; T-0203..T-0208 |
| CUI department templates (KCUIR, KIPS, KWLR, DU, TM, item categories) | another inspection system: item categories/types, attribute defs, templates with conditional fields, auto-raised issues, inspection-point repeats, report metadata, daily update, time management | registers notes §6–8 (concept mapping, capability gaps); T-0110, T-0111, E-04-05 |
| Earlier blueprint (QA/QC & operational execution platform) | dual inspection-driven / task-driven modes, readiness engine, role workspaces, multiple views (hierarchy/timeline/kanban), client collaboration portal, RFID/NFC/QR, selective offline sync, P6 import, AI insights | already covered by the master spec (MOD-05/06/08, edge screens, portal epics); kanban/timeline views now explicit in planner §9 and portal §2; QR scanning in scaffolding §8 |

## 2. New specs written from these inputs

- `docs/spec/modules/planner-and-work-plans.md` + ADR 0006 → E-04-12
- `docs/spec/modules/portal-plan-review.md` → E-07-04
- `docs/spec/modules/scaffold-management.md` → E-04-13
- `docs/spec/modules/change-of-scope.md` → E-04-14
- `docs/spec/modules/register-views.md` → E-01-01 (+ T-0020 criteria)

## 3. TODOs raised

All findings that change existing specs or tasks are raised as TODOs (see `docs/TODO.md`,
source `planner`, raised 2026-11-10), rather than edited into the existing module specs here, so each
lands with the task that owns it: **TD-080 … TD-111**. Planner/portal/scaffold decisions TD-080..TD-085; QA documents TD-086..TD-096; registers, competency, CUI system and time TD-097..TD-110; data risk TD-111.
