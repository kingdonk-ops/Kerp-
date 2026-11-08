# OpenBusinessERP — Per-Module Docs · Batch 4: Estimating & Cost

> Batch 4 of 7 — the largest domain (construction estimating, cost databases, 5D cost modelling, earned value). Four Pillars per module (compact where the module is a library/thin). Grounded in `backend/app/modules/<name>/`. `category` / `depends` / route counts are from the manifests/routers.

**Modules (26):** `boq`, `takeoff`, `dwg_takeoff`, `rebar_schedule`, `estimate_basis`, `estimate_rollup`, `rom_estimate`, `ai_estimator`, `costs`, `costmodel`, `cost_plan`, `cost_match`, `price_breakdown`, `price_index`, `norm_expansion`, `waste_factors`, `assemblies`, `prefab`, `measurement`, `value`, `eac`, `full_evm`, `cvr`, `project_controls`, `progress`, `resource_summary`, `resources`.

The pipeline: **takeoff → BOQ → assemblies/norms/costs (unit rates) → cost model (5D) → estimate basis/rollup → tender** (Batch 3), then on delivery **progress → EAC/EVM/CVR → postcalc** (Batch 1) feeds norms back.

---

### Quantities & takeoff
- **`boq`** — *core, depends[projects], 220 routes.* **What:** the Bill of Quantities — the central measured-scope spine. **Does:** BOQ structures, sections, items, quantities, rates, totals. **How:** the item tree carries measured quantities and unit rates; most estimating/tendering/progress modules read or roll up the BOQ. **Interactions:** source for `tendering`, `costmodel`, `progress`, `estimate_*`, `postcalc`. Core.
- **`takeoff`** — *extension, depends[projects, cad], 106 routes.* **What/Does:** PDF upload + quantity extraction (measure areas/lengths/counts off drawings into quantities). **How:** works with `cad`; produces measured quantities that feed the BOQ. **Failure:** disabled → no PDF takeoff; manual BOQ entry remains.
- **`dwg_takeoff`** — *extension, depends[projects], 52 routes.* **What/Does:** takeoff from DWG/CAD vector drawings. **How:** reads DWG geometry to quantities. Peer of `takeoff` for native CAD.
- **`rebar_schedule`** — *business, depends[users, projects], 30 routes.* **What/Does:** reinforcement bar scheduling/bending schedules → steel quantities and weights.
- **`measurement`** — *library (no router).* Quantity-determination helpers shared by takeoff/BOQ.
- **`waste_factors`** — *core, 14 routes.* **What/Does:** net-to-gross quantity adjustment (apply waste % to measured net quantities) — a library + config surface used across estimating.

### Cost base & unit rates
- **`costs`** — *core, no deps, 123 routes.* **What:** the **Cost Database** — the reference library of resource/material/labour/plant costs. **Does:** cost records, hierarchies, rate libraries. **How:** the unit-cost source assemblies and cost models price against. Core, widely depended on.
- **`assemblies`** — *core, depends[costs, fx], 42 routes.* **What/Does:** composite unit rates built from cost-database resources (labour+material+plant → a unit rate), FX-aware. **How:** an assembly sums its component resources × quantities × rates into a unit price.
- **`norm_expansion`** — *core, 20 routes.* **What/Does:** expand a **production norm** (output per crew-hour) into resource quantities for a work item — the no-code "norms" engine the product markets.
- **`price_breakdown`** — *library (no router).* Structured unit-price analysis (break a rate into its components).
- **`price_index`** — *core, depends[costs], 40 routes.* **What/Does:** base-to-current cost-index adjustment (escalate historic rates to today). **How:** applies index series to cost-database rates.
- **`cost_match`** — *core, depends[users, projects, costs, dashboards, ai], 26 routes.* **What/Does:** match a foreign/supplier bill onto the internal cost base (AI-assisted line mapping). **How:** maps external line text → cost-base items.

### Cost modelling & estimate assembly
- **`costmodel`** — *core, depends[projects, boq], 66 routes.* **What:** the **5D cost model** — cost dimension layered on the BOQ (and schedule). **Does:** model costs across quantity/time, scenario costing. **How:** ties BOQ quantities × rates across time for 5D (3D+time+cost).
- **`cost_plan`** — *business, depends[boq, projects], 4 routes.* **What/Does:** elemental cost plan (early-stage cost plan by element).
- **`rom_estimate`** — *core, depends[projects, costmodel], 14 routes.* **What/Does:** conceptual / Rough-Order-of-Magnitude estimates for early stages.
- **`ai_estimator`** — *core, no deps, 66 routes.* **What/Does:** AI Estimate Builder — draft an estimate from description/scope with AI assistance (human-confirmed).
- **`estimate_basis`** — *business, depends[boq, projects, allowances, preliminaries, costs], 12 routes.* **What/Does:** the **Basis of Estimate** — assumptions, inclusions/exclusions, qualifications behind the number.
- **`estimate_rollup`** — *business, depends[boq, projects, preliminaries, allowances], 2 routes.* **What/Does:** roll the BOQ + preliminaries + allowances into the headline estimate total.
- **`prefab`** — *business, depends[users, projects], 30 routes.* **What/Does:** off-site / Prefab / DfMA planning (modular/offsite scope and its cost/logistics).

### Resources
- **`resources`** — *business, depends[users, projects], 108 routes.* **What/Does:** graphical resource planning — crews/plant/labour allocation over time. **How:** schedules resources against activities; feeds payroll/field time.
- **`resource_summary`** — *core, depends[boq], 10 routes.* **What/Does:** summarise resource demand rolled up from the BOQ.

### Delivery: earned value & reconciliation
- **`progress`** — *core, depends[projects, boq], 18 routes.* **What/Does:** site-execution measurement (% complete against BOQ). **How:** progress claimed against measured BOQ items → earned quantity. Feeds EVM/CVR/site_inventory.
- **`eac`** — *core, depends[users, projects], 58 routes.* **What:** **Estimate At Completion** platform (EAC v2). **Does:** forecast final cost from actuals + remaining. **How:** combines committed/actual cost with remaining work to project EAC.
- **`full_evm`** — *enterprise, depends[finance, eac, projects], 40 routes.* **What/Does:** full Earned Value Management (PV/EV/AC, CPI/SPI, variances). **How:** builds on `eac` + `finance` actuals.
- **`cvr`** — *business, depends[users, projects], 54 routes.* **What/Does:** Cost-Value Reconciliation & cashflow (the UK commercial-reporting view: value vs cost vs cash).
- **`value`** — *controls, library-ish, 16 routes.* **What/Does:** "Value Realized" — track realised value/benefits (controls/governance view).
- **`project_controls`** — *extension, depends[projects, users, bi_dashboards], 4 routes.* **What/Does:** executive cross-module controls dashboard aggregating cost/schedule/risk.

---

### Batch-4 pipeline map
```
takeoff / dwg_takeoff / rebar_schedule / measurement ──quantities──▶ boq
boq ──×rates── costs → assemblies / norm_expansion / price_breakdown / price_index / waste_factors
   │                                   │
   └──▶ costmodel (5D) ──▶ estimate_basis + estimate_rollup (+ preliminaries, allowances) ──▶ tender/bid
rom_estimate / ai_estimator / cost_plan ──early estimates──▶ (feed the above)
resources / resource_summary ──allocate──▶ payroll / field time
DELIVERY:  progress ──earned──▶ eac ──▶ full_evm ; cvr (cost/value/cash) ; value ; project_controls (exec rollup)
cost_match ──foreign bill → cost base ; postcalc (Batch 1) ──actuals──▶ norms
```
`boq` and `costs` are the always-on cores everything else reads; each estimating/EVM add-on gates independently (disable → its routes 402, the BOQ/cost base keep working).

*Batch 4 of 7. Next: **Project Delivery & Field** (schedule, daily_diary, inspections, ncr, safety, punchlist, commissioning, closeout, defects_liability, hse_advanced, temporary_works, formwork, equipment, assets, fieldreports, field_time, field_diary, site_*). Grounded @ `a96d525`.*
