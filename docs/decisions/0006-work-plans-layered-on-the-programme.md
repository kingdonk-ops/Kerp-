# ADR 0006 — Work plans are a KERP layer on the P6 programme (overlay / interrupt)

- **Status:** proposed
- **Date:** 2026-11-10
- **Resolves (on acceptance):** TD-080
- **Affects:** E-04-01 (schedule), E-04-02 (look-ahead), E-04-07 (tasks), E-04-08 (resource_summary), E-04-12 (planner, new), E-07-04 (portal plan review, new), T-0106 (P6 export)
- **Builds on:** ADR 0003 (work anchor by role), ADR 0005 (field ownership, P6 owns P6 fields)

## Context

Planners need to prepare jobs — typically a shutdown or break-in — while the main P6 programme
is running, get the client to approve them, and then have them "added to the schedule". Two
patterns occur on site (reference: client shut schedule, P6 schedule with one calendar per shut
window, a one-week shut resource allocation sheet):

1. the job runs **alongside** the programme on the same people (overlay);
2. on its start day the normal programme **stops** (for the whole project, an area, or some
   trades), the job runs, and the programme **carries on afterwards** (interrupt).

P6 is the system of record for the schedule (P6 spec §1). KERP must not write P6-owned fields,
and nothing is pushed to P6 automatically.

## Decision

1. **Plans are KERP-owned records** (`planner` module) with their own revisions and approval.
   Until committed they affect nothing but forecasts shown in "what-if" views.
2. **The programme has two layers**: the P6 layer (imported, read-only for P6-owned fields)
   and a KERP layer (committed plan items + forecast overrides). Every programme view merges
   both and labels the source. P6 re-imports never touch the KERP layer (ADR 0005 principle 2).
3. **Overlay** plans add demand and items; programme dates are unchanged.
4. **Interrupt** plans store a *displaced forecast* for the affected programme activities in
   the KERP layer: remaining work is pushed past the window on its own calendar, successors
   follow their P6 logic. Baseline and P6 dates are never changed.
5. **Commit** writes the KERP layer, creates tasks and resource bookings, and queues a P6
   fragnet / forecast export for the scheduler. When the scheduler later re-imports a P6
   schedule that contains those activities, the importer matches them by KERP id
   (`external_id` round trip) and the KERP-layer copy is marked `superseded_by_p6` — no
   duplicates.
6. Approval is per **revision**, bound to the revision's content hash; any edit after issue
   creates a new revision (portal spec §7.4).

## Consequences

- `schedule` (E-04-01) needs the layered model and a forecast-override table; `T-0106` export
  gains a fragnet variant; the P6 importer gains the KERP-id match rule (P6 spec §6 extension).
- Look-ahead and `resource_summary` read bookings from committed plans.
- Cost: a forward-pass scheduler over P6 logic for the displaced forecast (bounded to the
  affected sub-network; SAQ batch queue).
- Risk: planners may treat the KERP layer as the master schedule. Mitigation: every view shows
  "not yet in P6" badges until superseded by a re-import.

## Alternatives rejected

- **Edit P6 dates in KERP** — breaks ADR 0005 field ownership and the client's P6 control.
- **Plans only as documents (PDF) with no schedule effect** — the user requires approved plans
  to be added to the schedule and viewed overlaid.
- **Full scheduling engine replacing P6** — out of scope; P6 stays the master.
