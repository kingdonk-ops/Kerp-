# Module detail — Material control, receiving & heat traceability (`materials`)

Covers MOD-07. Supply-chain, positive material receiving and metallurgical trace from mill to
installed spool. Commitments only — money is an external read-only mirror.

## Scope
- **Purchase requisitions / orders** (`procurement`, external commercial refs) and material
  delivery schedules.
- **MRIR** (Material Receiving & Inspection Reports): quantities received, shipping damage,
  **MTR / mill certs**, quarantine holds.
- **Metallurgical trace:** heat number, melt number, flange rating, plate thickness, alloy
  type; unique internal trace barcode.
- Warehouse bins, laydown-yard coordinates, bag-and-tag staging for IWPs.

## Key logic
- **MTR OCR** runs in the **SAQ `interactive`** queue → extracts chemistry/mechanicals
  (yield, tensile, carbon equivalent %CE) → writes weld joint properties in `wps`.
- Barcode/QR scanning (Bluetooth handhelds + tablet camera).
- MRIR acceptance clears the AWP **material constraint** when 100 % of an IWP's line items are
  staged; heat numbers link to every weld execution id.
- MTRs + inspection certs auto-compile into the **MDR** ([`documents-and-mdr.md`](documents-and-mdr.md)).
- **Client-supplied (free-issue) material** (product intake 2026-11-22 (3), planner default;
  user to confirm; TD-593): material lines and lots carry `supplied_by` (`kaefer | client`, the
  same flag as weld joint sides in welding-and-nde.md, TD-091) and, for client-supplied lines,
  an `expected_delivery` date. A client-supplied line expected after the planned start of the
  work that installs it raises a warning readiness conflict (quality-and-inspection.md
  §Schedule readiness conflicts), never a block.

## MTR verification gate (user, 2026-11-21; planner defaults; T-0265, Phase 2)
- A heat/MTR record (T-0205) carries `verification_status` (`pending_verification | verified |
  rejected`). It stays `pending_verification` until QA verifies the uploaded MTR PDF through the
  unified verification queue (`quality-and-inspection.md` §QA verification queue and two-lane
  workbench; `target_kind = material_mtr`).
- Rule set `material_cert.verify` (locked system-default `check`, ADR 0004 §5): a weld or spool
  that uses a heat without a verified MTR cannot be accepted or released (`run_checks`). The
  client-supplied-steel waiver (TD-091, welding-and-nde.md) is unchanged.
- Creating a heat batch without a certificate notifies the project's QA reviewer persona (high
  priority). An MTR uploaded by a user without QA authority stays pending even when SAQ OCR
  matches; the QA reviewer may verify their own upload at once (audited).
- The two-lane window shows heat chemistry/mechanical values next to the OCR values and the MTR
  history of the lot. MRIR receiving (E-03-03) reuses this gate.
- **Use-by dates** of stock lots (coatings, epoxies, electrodes; E-03-03/E-03-04) feed the expiry
  lead-time alerts and schedule readiness conflicts (`quality-and-inspection.md` §Expiry
  lead-time alerts, §Schedule readiness conflicts) through a materials provider.

## Shelf-life extension (user, 2026-11-21; planner defaults; T-0184 and T-0268, Phase 3)
Material batches/lots with a use-by date (coatings, epoxies, electrodes) are defined when E-03-03 /
E-03-04 are broken down (Phase 3; TD-421); the batch carries `original_use_by_date` (immutable
once set) and `effective_use_by_date` (equal to the original until an extension is approved,
changed only by an approved extension).
- **Extension** `oe_material_shelf_life_extension` (`BaseRecordMixin` + project RLS):
  `material_batch_id`, `extension_seq` (1, 2, …), `previous_expiry_date`, `proposed_expiry_date`
  (CHECK proposed > previous), `reason_notes` (mandatory, non-blank), `document_id` (mandatory,
  a complete T-0016 upload: manufacturer concession letter or Quality Engineer evaluation),
  `issuer_kind` (`manufacturer | quality_engineer`), requester and time, `status`
  (`pending_approval | approved | rejected`), approver and time, `rejection_reason`; unique
  `(project_id, material_batch_id, extension_seq)`; index `(project_id, material_batch_id,
  status)`. One pending extension per batch at a time.
- **Approval** through the QA verification queue (`target_kind = material_shelf_life_extension`):
  the approver is the project's QA reviewer persona (default PER-06); when the requester holds
  PER-06, a PER-13 Quality Director with access to the project (user, 2026-11-21; persona-matrix
  check TD-422). Nobody approves their own request; `allow_self_approval` (T-0140) is ignored
  for extensions (always false). Approve sets `effective_use_by_date`, makes the batch eligible
  again (as-of-date eligibility, T-0139, through the materials provider), re-evaluates readiness
  conflicts and expiry alerts (T-0183, T-0266) and writes original date, new date, document
  SHA-256, requester and approver to the ledger. Reject keeps the batch blocked, needs a reason
  and notifies the requester.
- **Gate:** selecting a batch on an inspection form evaluates
  `material.effective_use_by_date >= context.inspection_date` on client and server (CEL); an
  expired batch fails eligibility and blocks acceptance of the inspection.
- **Offline** (T-0268): the sync reference bundle (T-0147 contract, unchanged) gains active
  batches (batch/heat number, original and effective use-by) and the approved extension documents
  and MTRs of those batches, downloadable for offline viewing.
- **MDR:** the dossier attaches the original MTR/batch certificate and every approved extension
  certificate in chronological order (`documents-and-mdr.md` §Material traceability in the MDR).

- **Attested approval (enterprise roadmap, 2026-11-21).** QA approval of an extension is a formal
  sign-off (`shelf_life_extension_approve`): re-authentication, intent statement and evidence in
  the sign-off ledger (`quality-and-inspection.md` §Signing attestation; T-0184 AC, T-0289).
  A delegate may approve only with their own QA persona and credentials (§Delegation of authority).

## Product substitutions (user, 2026-11-21; planner defaults; T-0185, T-0269–T-0273)
A register of approved substitutes for specified products (coatings, consumables, components),
with traceable authority, used by the product-spec check on inspections.
- **Record** `oe_material_substitution` (`BaseRecordMixin` + project RLS): `ref`
  (`{project}-SUB-{seq:4}`, project numbering pattern, T-0108), `approval_route` (`client_tq |
  internal_qa`), `tq_id` / `tq_ref`, `scope_kind` (`project | node_subtree | work_anchor |
  item`) with `scope_node_id` / `scope_item_id`, original and substitute product code and name,
  `justification` (mandatory), `equivalency_document_id` (manufacturer equivalency letter,
  datasheet or engineering memo, T-0016), `status` (`draft | pending_approval | approved |
  rejected | revoked`), requester, approver, `approval_signature_id` (T-0119 sign-off ledger),
  `rejection_reason`; index `(project_id, status, original_product_code,
  substitute_product_code)`.
- **Baseline list** `oe_material_approved_product` (T-0269; TD-424): approved products per
  specified product code, seeded from client packs, maintained by PER-06/PER-09.
- **internal_qa route** (Phase 1): approvers are the project's `substitution_approver_personas`
  (default PER-06; PER-13 per TD-422). The approver differs from the requester unless the project
  allows self-approval (TD-423), in which case the record is flagged `self_approved`. Approval
  needs the equivalency document or a documented engineering rationale, signs through T-0119 and
  is written to the ledger. A draft by anyone else goes to the QA verification queue
  (`target_kind = material_substitution`).
- **client_tq route** (Phase 5, T-0273, with the TQ register E-05-04): linked to a TQ, approved
  when the TQ is approved or with recorded client approval evidence.
- **Product check** (T-0270; TD-425): the eligibility service resolves `context.product_approvals`
  (baseline list + approved substitutions whose scope covers the record's anchor, approved on or
  before the inspection date and not revoked by then); rules call the pure CEL function
  `is_product_approved(approvals, spec_product_code, selected_product_code)`, identical on both
  runtimes. An unapproved alternative fails the rule; other rules still run.
- **Field use** (T-0271, T-0272): approved substitutions, the baseline list and equivalency
  documents are in the offline reference bundle; the tablet selector badges substitutes
  "Approved Substitute ({tq_ref or ref})"; selecting one binds `substitution_id` on the answer
  and adds the read-only note "Product substitution applied: {substitute} used in place of
  {original} per {tq_ref or ref}" to the inspection and its PDF.
- **MDR:** every inspection or asset that references a `substitution_id` brings the approved
  substitution record (or the linked TQ approval) and its equivalency document into the dossier
  (`documents-and-mdr.md` §Material traceability in the MDR).

- **Attested approval (enterprise roadmap, 2026-11-21).** The internal_qa approval is a formal
  sign-off (`substitution_approve`, T-0291) and the TQ approval of the client_tq route is one too
  (`tq_approve`, Phase 5); see `quality-and-inspection.md` §Signing attestation.

## Cutting interface
Confirmed cutting plans post net stock consumption + new offcuts to `site_inventory`; the
optimiser draws from available offcuts before new stock (1D **FFD**, 2D **rectpack** — GPL
`libnest2d`/`nest2D` banned).
