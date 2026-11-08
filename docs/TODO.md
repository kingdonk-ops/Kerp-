# KERP — TODO: decisions · spec gaps · missed · deferred · later

> **Generated** by `python scripts/tracker.py render` from `tracker/todo/*.md` — do not edit by hand.
> Add: `python scripts/tracker.py new-todo --kind <kind> --title "..." --task T-xxxx --source <agent>`

**Open:** 10 · **Closed:** 0

## decision (5)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-001](../tracker/todo/TD-001.md) | Port Inspection-ERP's proven core (BaseRecordMixin, RLS, auth, audit, loader, validation) or rewrite from the spec? | T-0005 | spec-review |
| [TD-002](../tracker/todo/TD-002.md) | Adopt PROPOSAL-port-from-aip (one forms/item_types engine, eligibility service, inspection_requests)? | T-0102, T-0109, T-0110, E-01-01 | spec-review |
| [TD-003](../tracker/todo/TD-003.md) | Identity: local JWT only, or SSO (Entra ID / Windows) for staff + email/password for invited clients? | T-0007 | spec-review |
| [TD-004](../tracker/todo/TD-004.md) | Hosting target: Coolify/MinIO (ADR-001) vs AWS (S3, SES) — affects storage/email adapters and deploy | T-0015 | spec-review |
| [TD-007](../tracker/todo/TD-007.md) | 'Single company' vs tenant_id + RLS everywhere — keep multi-tenant plumbing with one tenant per deploy? | T-0006 | spec-review |

## spec-gap (4)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-005](../tracker/todo/TD-005.md) | module_builder (runtime/no-code modules) has no buildable spec | T-0027 | spec-review |
| [TD-006](../tracker/todo/TD-006.md) | Offline sync listed in Phase 4 modules but plan §9 requires it from Phase 0/1 — moved core to Phase 1 (T-0125/T-0126) | T-0125 | spec-review |
| [TD-008](../tracker/todo/TD-008.md) | Tier-1 edge screens (Gate Delivery, Look-Ahead Board, Daily Workface Log) have no UI specs; architecture-suite Part 4 describes the as-built Inspection-ERP UI | — | spec-review |
| [TD-010](../tracker/todo/TD-010.md) | Architecture suite (Inspection-ERP PR #22) is still open/unmerged — imported from its branch as reference only | — | spec-review |

## risk (1)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-009](../tracker/todo/TD-009.md) | Scope: ~94 modules. Define an MVP cut line (suggest: end of Phase 2 + documents) for a first pilot | — | spec-review |
