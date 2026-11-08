# Legacy specification archive — frozen, NOT a build target

These documents are **historical reference only**. They are the pre-consolidation
analysis, module notes and port templates that preceded the unified master spec. They
are kept so an obscure industrial calculation or entity/field detail can be looked up,
but no agent builds to them.

**The single source of truth is [`docs/spec/`](../spec/README.md)** — specifically
`KERP-MASTER-SPEC.md` and `KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md`. If anything here
disagrees with those two documents, the two documents win.

| Folder | What it was |
|---|---|
| `architecture-suite-reference/` | Inspection-ERP PR #22 — a reverse-engineered description of the as-built Inspection-ERP (stack, base/extension modules, frontend/UX). The port template, never KERP scope. (Was `docs/archive/pr-22-architecture-suite-NOT-SPEC/`.) |
| `field-operations-platform/` | The field-ops pivot plan, MODULE-SPECS, ADR-001 runtime, workpacks/AWP, the AIP one-engine proposal (PROPOSAL-port-from-aip) and MODULE-SPEC-11. Superseded by the master spec; task bodies may still cite its section numbers (e.g. "SPEC-11 §2") for provenance. |
| `openbusinesserp-analysis/` | The original full-ERP analysis and module catalog. Many modules were removed in the field-ops pivot; superseded by the master spec. |

Do not add these paths to agent context or new task `spec:` references.
