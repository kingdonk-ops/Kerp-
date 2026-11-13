# ADR 0005 — Field checkout, field ownership and per-field sync-back against P6 re-imports

- **Status:** accepted (user, 2026-11-10)
- **Date:** 2026-11-10
- **Resolves (on acceptance):** TD-075
- **Spec:** [`docs/spec/modules/p6-import-and-rsw-matching.md`](../spec/modules/p6-import-and-rsw-matching.md) §7–§8
- **Affects:** T-0104, T-0105, T-0126, T-0127, T-0129, T-0131 (M1 freeze), new T-0133
- **Amends:** master spec §1.3 rule 3 and §4.3 (LWW no longer applies to field-owned values of a checked-out record); supersedes the §1.3 whole-payload envelope shape for tablet mutations (per-path `changes` with base hashes, spec §8.2)

## Context

Inspectors check inspection jobs out to a tablet and work offline, sometimes for days
(master spec: up to 7 days). Planners re-import the P6 schedule on their own cycle. The
requirement (user, 2026-11-10): *if a job is checked out on a tablet and a P6 import updates the
records in the meantime, only the changes made on the tablet are synced back.*

The current spec does not guarantee that:

- §1.3 uses **last-write-wins** on "non-critical text fields". A tablet that pushes a whole
  record would overwrite every P6 change made during the checkout.
- §4.3 rule 2 merges "non-conflicting attributes" but does not say how the server knows which
  attributes the tablet changed.
- Nothing says which fields an import may write and which the tablet may write.
- TD-035 decided a record-level `base_revision` check on sync ingest. Record-level alone would
  reject a tablet's whole batch whenever an import touched the record, which is the normal case.

## Decision

1. **Field ownership.** Every writable field belongs to exactly one owner: `p6`, `office` or
   `field` (spec §7). The map is declared in the field registry and frozen at M1. The P6 import
   writes `p6` fields only; a tablet writes `field` fields only.
2. **Checkout lease.** `oe_field_checkout` records user, device, lease expiry, per-record
   `base_revision` and the pinned `template_revision_id`. One active checkout per record. Office
   edits to `field` fields of a checked-out record return 409 unless a supervisor force-releases.
3. **Per-field mutations.** Outbox mutations carry only the changed paths, each with the hash of
   the value it was based on (spec §8.2). Whole-record pushes are not accepted from tablets.
4. **Per-field three-way apply.** For each path: not field-owned → reject that path; record
   locked → reject; server value unchanged since base → apply; server value changed → keep
   server value, log the tablet value in `sync_conflict_log`, raise a review item (spec §8.3).
   Untouched paths are never written. Child collections are additive.
5. **TD-035 refined.** `base_revision` stays on the envelope for idempotency and diagnostics, but
   the accept/reject decision is per field, not per record.
6. **Schedule changes never discard field work.** Removed or cancelled activities flag the node
   for review; the tablet's data is still accepted (spec §8.4).

## Consequences

- T-0126 implements per-field apply, ownership checks and the conflict log; T-0127 tracks dirty
  paths and base hashes in Dexie; T-0104 writes `p6`-owned fields only; T-0129 shows checked-out
  records on the change report; new T-0133 adds the checkout lease and the end-to-end contract
  test (spec §11 tests 10–15).
- Field ownership becomes part of the M1 freeze (T-0131).
- LWW remains only for `office` text edited concurrently by two desktop users (with conflict log).
- Cost: the client must keep a base hash per edited path (small); the server compares hashes per
  path (cheap). Office users see a 409 on checked-out records — acceptable, and visible.

## Alternatives rejected

- **Record-level LWW** — loses P6 changes made during the checkout. Violates the requirement.
- **Record-level optimistic lock only** — rejects nearly every tablet batch after any re-import.
- **Freeze P6 import while anything is checked out** — blocks planners for days; does not scale.
- **Hold the whole import until checkouts return** — same problem, and hides schedule changes from
  the tablet.

## Acceptance (user, 2026-11-10, TD-075)

Accepted as proposed. Office edits to field-owned values of a checked-out record are
**blocked** (not queued) until the lease is returned or released.

## Addendum A — `node` checkout scope and lease length (planner defaults, pending user confirmation (TD-162); 2026-11-13, Pilot-0)

Planner defaults, recorded with the Pilot-0 sync scope (`docs/spec/KERP-CONSOLIDATION-AND-DIFF.md`
§6.4); the accepted decision above is unchanged.

- **New scope `node`.** Besides an inspection job / RSW batch, a checkout may name an anchor
  subtree (`node`). It checks out every inspection under the node that nobody else holds (partial
  pick) and downloads the subtree's Items, scan identifiers and reference data read-only. One
  active checkout per record still holds: a record already held is skipped and reported with its
  holder; a single-record pick of a held record is refused with 409 naming the holder.
- **Lease length.** The lease lasts the deployment's offline window (default 7 days, max 14) and
  is renewed on each successful sync of the holding device. Expiry neither removes nor purges the
  pick on the device; later data follows p6 spec §8.5.

