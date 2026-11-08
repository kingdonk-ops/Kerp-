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

## Cutting interface
Confirmed cutting plans post net stock consumption + new offcuts to `site_inventory`; the
optimiser draws from available offcuts before new stock (1D **FFD**, 2D **rectpack** — GPL
`libnest2d`/`nest2D` banned).
