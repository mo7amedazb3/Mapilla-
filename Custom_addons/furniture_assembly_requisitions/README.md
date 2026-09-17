# طلب الاذونات

Opt-in department supply app. It gives every active production stage except
priming a shared hall raw-material balance (initial rollout: yasser3).

## Workflow

- Painting, assembly, bases, finishing, tailoring, upholstery and packaging
  supervisors get the standalone app. A production manager configures the exact
  storable products each supervisor may request for each stage. Only those products
  appear, and server-side validation prevents forged products or stage access.
- Submitted requests appear under the storekeeper's existing Warehouse Permissions
  app and create durable activities plus the existing real-time notification.
- Approval atomically transfers unreserved Stock inventory to the selected
  stage hall. A retry does not transfer again; rejection moves nothing.
- Approved external quantities form a stage/product supply pool. Each normal
  per-order request subtracts only the still-unallocated external quantity from
  its exact recipe requirement. For example, a 10 m requirement with 5 m supplied
  externally produces a normal request for the remaining 5 m.
- Normal stage request/issue/receipt buttons and bulk selectors remain enabled.
  Their warehouse snapshots retain partially covered products with only the
  remaining quantity, so one product can safely use both supply paths.
- Stage work appears only when the existing upstream product handoff and the
  normal material receipt allow it. Starting consumes both manually supplied
  and normally issued materials exactly once, hall → Production, with
  material-line and production-line stock traceability. Insufficient combined
  hall stock blocks starting. Priming retains its existing behavior unchanged.

## Implementation boundaries

The addon records external coverage on each normal release-stage snapshot. That
allocation prevents two production orders from consuming the same external supply,
while the original full recipe is still consumed once from the stage hall.

Fixed recipe units preserve the current MRP conversion convention. Existing
same-category conversions use unrounded Odoo conversion. Legacy different-category
recipe units preserve the numerical quantity as the existing MRP code does.
No product units, recipes or stock quantities are changed during installation.

## Safety / maintenance

- Company-scoped ACLs and record rules. Only production managers maintain the
  supervisor/material matrix. A supervisor may create requests only for the exact
  stages and products assigned to that user; storekeepers approve via their app.
- Row/advisory locks plus available-stock and protected-MRP-reservation checks.
- Historical approvals and stock moves are retained. Initial yasser3 audit found
  no active per-order assembly requests/batches or reservations to migrate.
- Do not uninstall while active stage work or requisitions exist. Restore
  the pre-install backup for a complete rollback; uninstall is not a stock reversal.

Run `/furniture_assembly_requisitions` Odoo tests on a neutralized disposable clone,
with a dedicated HTTP port and cron disabled. Never test inventory on production.
