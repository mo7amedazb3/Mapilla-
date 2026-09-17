# Independently executed textile kits

Implemented in models/textile_kit_execution.py, with the private allocation
adapter in models/textile_kit_preview.py and frontend dispatch in
static/src/js/textile_kits.js. Enable per database using configuration parameter
furniture_need_to_produce.textile_kits_enabled=True after schema upgrade.

## Operational contract

- One persistent kit has exact source production-line members, its own timer
  and independent start/pause/resume/finish. Original orders remain intact for
  inventory, material requests and traceability; no MO numbers on kit cards.
- Actual phantom BOM ratios determine a complete kit. Scope includes company,
  stage, model, buyer, beneficiary and compatible component setup. Custom and
  incomplete remainders stay separate.
- Source splitting preserves completed progress and material allocations.
  Sorted source/release locks, immutable snapshots and unique line/stage
  membership prevent duplicate allocation. Allocated rows cannot be
  consolidated or have their quantity changed.
- Real material receipt and arrival remain required. Finish affects only that
  kit's members and requires tailoring quality. Sibling worker logs remain
  open. Tailoring internal steps close only after the final component finishes.
  Loose remainders use exact-line operations.
- Approved-plan handoffs validate the aggregate original quantity and identity
  across split rows. Each input claim reserves available capacity on the exact
  target row; source outputs and outstanding supply claims follow split
  lineage. An already-reserved received batch cannot be split blindly.
- Temporary fixed-hour orders retain their continuous-clock timer behavior;
  normal production continues to use factory working hours.
- Compact expandable cards retain the existing theme. Large sofa first,
  other pieces next, fauteuil last. Material amounts and planned time belong
  to the kit's actual quantities, not the original whole batch.

## Regression checks

- tests/test_kit_card_allocation.py: 11 deterministic allocation, conservation,
  scope, custom, ambiguity and limit cases.
- Odoo post-install test /furniture_need_to_produce:TestTextileKitExecution:
  two kits sharing two source orders; independent start/pause/finish; manual
  quality; direct-write and foreign-member denial; idempotent finish; loose
  remainder completion. Test production and stock changes are rolled back.
- test_textile_kit_splits_preserve_exact_planned_handoffs traverses the full
  approved dependency chain after splitting tailoring and upholstery rows,
  checks every target row receives its input and retries create no duplicates.
- Browser checks: both stages, admin and supervisor, 11 kit plus 3 loose cards,
  three products per complete kit, compact expansion, no MO references and
  large-sofa-first product order.

## Existing-data migration, 2026-09-10

The isolated clone textile_kit_verify_20260910 reproduced yasser3's 48 pending
pieces from 10 source orders in each textile stage. Migration yields 6 Marly
and 5 Big Moon kits per stage; remainder: one large sofa, two fauteuil and one
custom chaise in three cards. All 48 pieces remain in each stage.

Before/after checks compare quantities per source, material totals per
source/stage/material/UOM, stock move/valuation/cost counts and every stock
quant quantity/reservation. Migration never starts or receives production.

Backup and verification scripts:
 /opt/odoo/backups/textile_kits_live_20260910.TYnDfa/

migrate_verify.py explicitly allows only the clone and yasser3, checks these
expected quantities and commits only after all checks pass. Other installed
manufacturing databases receive the schema upgrade, not this yasser3-specific
migration or feature flag. Wood does not have the module installed and is not
changed.
