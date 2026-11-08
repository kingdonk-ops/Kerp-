# KERP — TODO: decisions · spec gaps · missed · deferred · later

> **Generated** by `python scripts/tracker.py render` from `tracker/todo/*.md` — do not edit by hand.
> Add: `python scripts/tracker.py new-todo --kind <kind> --title "..." --task T-xxxx --source <agent>`

**Open:** 11 · **Closed:** 2

## decision (4)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-001](../tracker/todo/TD-001.md) | Port Inspection-ERP's proven core (BaseRecordMixin, RLS, auth, audit, loader, validation) or rewrite from the spec? | T-0005 | spec-review |
| [TD-003](../tracker/todo/TD-003.md) | Identity: local JWT only, or SSO (Entra ID / Windows) for staff + email/password for invited clients? | T-0007 | spec-review |
| [TD-004](../tracker/todo/TD-004.md) | Hosting target: Coolify/MinIO (ADR-001) vs AWS (S3, SES) — affects storage/email adapters and deploy | T-0015 | spec-review |
| [TD-007](../tracker/todo/TD-007.md) | 'Single company' vs tenant_id + RLS everywhere — keep multi-tenant plumbing with one tenant per deploy? | T-0006 | spec-review |

## spec-gap (4)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-005](../tracker/todo/TD-005.md) | module_builder (runtime/no-code modules) has no buildable spec | T-0027 | spec-review |
| [TD-006](../tracker/todo/TD-006.md) | Offline sync listed in Phase 4 modules but plan §9 requires it from Phase 0/1 — moved core to Phase 1 (T-0125/T-0126) | T-0125 | spec-review |
| [TD-008](../tracker/todo/TD-008.md) | Tier-1 edge screens (Gate Delivery, Look-Ahead Board, Daily Workface Log) and the app shell/design system have no UI specs in PRs 23/24 | — | spec-review |
| [TD-012](../tracker/todo/TD-012.md) | audit log (T-0013) has thin spec coverage — only DEVELOPMENT-PLAN principles 3/10/11 | T-0013 | planner |

## deferred (1)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-011](../tracker/todo/TD-011.md) | forms F13 report-slot mapping + F14 AI import assistant (SPEC-11 §2) not in Phase 1 tasks | T-0131 | planner |

## risk (2)

| ID | Title | Related tasks | Raised by |
|---|---|---|---|
| [TD-009](../tracker/todo/TD-009.md) | Scope: ~94 modules. Define an MVP cut line (suggest: end of Phase 2 + documents) for a first pilot | — | spec-review |
| [TD-013](../tracker/todo/TD-013.md) | review-gate patch-id ignores tracker/ — on planning PRs task content can change after approval without re-review | — | coordinator |

## Closed

| | ID | Title | Resolution |
|---|---|---|---|
| ✅ | [TD-002](../tracker/todo/TD-002.md) | Adopt PROPOSAL-port-from-aip (one forms/item_types engine, eligibility service, inspection_requests)? | Adopted — PR #24 is part of the spec (user, 2026-11-08) |
| ⚫ | [TD-010](../tracker/todo/TD-010.md) | Architecture suite (Inspection-ERP PR #22) is still open/unmerged — imported from its branch as reference only | PR #22 is not a spec input; moved to docs/archive |
