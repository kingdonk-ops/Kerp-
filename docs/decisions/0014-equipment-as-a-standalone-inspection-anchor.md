# ADR 0014 — Equipment as the anchor of standalone inspections (amends ADR 0003)

- **Status:** **superseded** (user, 2026-11-21) by the
  [ADR 0003 amendment 2026-11-21](0003-configurable-breakdown-and-terminology.md) (section "Amendment 2026-11-21"):
  equipment is an informational **link** of a project-level inspection, not a third exclusive
  anchor. TD-411 resolved; TD-412 resolved 2026-11-21 (fleet vehicles and plant join the Phase-1 register, T-0249). The text below is
  kept as history and is **not** in force.
- **Date:** 2026-11-21
- **Affects:** T-0250 (blocked by TD-411), T-0249 (TD-412, resolved 2026-11-21), T-0111 (anchor rule; no
  change to its scope), T-0112 (equipment register), CLAUDE.md §6 anchor wording,
  `quality-and-inspection.md` §Standalone inspections

## Superseding decision (user, 2026-11-21)

- No `equipment_id` anchor on `oe_inspections_inspection`; inspections of every area other than
  `qa_itp` are project-level with an optional `location_node_id` and links
  (`oe_inspections_link`, `target_kind = equipment` among others; T-0325). At most one equipment
  link per record (planner default) is the `equipment` context of post-submit automations
  (T-0250, T-0288).
- The equipment checks of Decision point 4 below still apply to the **link**: unknown or other
  project → 404; decommissioned → 422; out-of-service or quarantined → 422 except for
  `repair_fault` (T-0250).
- Decision point 5 below (CLAUDE.md wording) is replaced by point 7 of the ADR 0003 amendment.

## Context

ADR 0003 §6 and Amendment 5 make inspections anchor to any node whose level has the
`inspection_anchor` role, or an Item; T-0111 enforces exactly one of `node_id` / `item_id`.
CLAUDE.md §6 calls the anchor model law.

The user's 2026-11-21 paste (pasted T-0179) adds standalone inspections: daily pre-starts,
equipment checks and repair/fault reports on fleet vehicles and plant (`oe_equipment_asset`).
A vehicle is not part of the physical breakdown and has no node or Item. The equipment register
is the `equipment` module (T-0112, diff §5); today it holds calibrated test equipment only
(fleet and plant: E-03-07, Phase 3; TD-412).

## Decision (proposed)

1. `oe_inspections_inspection` gets a nullable `equipment_id` (additive; no FK: the register
   belongs to the `equipment` module and is read only through its `service.py`).
2. The anchor rule becomes: **exactly one of `node_id`, `item_id`, `equipment_id`**.
   `equipment_id` is allowed only when `is_standalone = true` and `category` is
   `safety_prestart`, `equipment_check` or `repair_fault`. `kind = itp` records and
   `category = qa_itp` keep the ADR 0003 anchors unchanged. A violation is 422.
3. An equipment-anchored inspection may carry an optional `location_node_id` (any node) for
   where it was done. It is informational: it is not an anchor and never feeds work-anchor
   completion, progress or the node inspection roll-up of T-0111.
4. The equipment must exist in the project and be selectable (the equipment service decides;
   decommissioned → 422; out-of-service equipment may still receive a `repair_fault`
   inspection).
5. CLAUDE.md §6 wording becomes: "inspections to any `inspection_anchor` level or an Item, or —
   standalone equipment inspections only (ADR 0014) — an equipment register entry. No BOQ anchor
   anywhere."

## Consequences

- The M1 freeze (T-0131) freezes the three-way anchor check if this lands before the freeze;
  later it needs an ADR amendment, as any post-freeze change.
- Register views and the Pilot-0 tiles must not count equipment-anchored inspections as
  QA progress (they are filtered by `category = qa_itp` by default, T-0251).

## Alternative

Model each vehicle or plant item as an Item (`oe_asset_item`) under an `inspection_anchor`
node: no ADR 0003 change, but fleet then lives in the physical breakdown and duplicates the
equipment register.
