---
type: "query"
date: "2026-08-09T15:42:18.611389+00:00"
question: "Review safe post-migrate 18.0.2.2.142 for folding standalone sewing into tailoring on yasser3 without breaking unique history"
contributor: "graphify"
outcome: "corrected"
correction: "Use direct Odoo model/selection/constraint code plus live database SQL for migration design. Never bulk-update advance-release, store-request, or stage-cost sewing history to tailoring because same-key counterparts exist; normalize only route flags/material tables, transfer employee M2M with ON CONFLICT, archive exact resources by XMLID, and unlink only module-owned sewing UI records."
source_nodes: ["FurnitureMrpProductionOrder", "FurnitureMrpStageMixin"]
---

# Q: Review safe post-migrate 18.0.2.2.142 for folding standalone sewing into tailoring on yasser3 without breaking unique history

## Answer

Expanded from graph vocab: [sewing, tailoring, stage, substages, material, location, employee, action, menu, report, transaction]. Graph traversal selected generic TransactionCase nodes and did not answer migration safety. Direct model code and live yasser3 SQL proved that three releases contain both tailoring and sewing for the same release/production and 47 cost lines have both stages, so audit stage codes must remain legacy sewing; only flags and operational material-route rows should be normalized. Locations 325/326 have no quants or moves and workcenter_stitching has no workorders, so archive by XMLID. Existing standalone sewing model and records must remain registered while its owned UI records are unlinked.

## Outcome

- Signal: corrected
- Correction: Use direct Odoo model/selection/constraint code plus live database SQL for migration design. Never bulk-update advance-release, store-request, or stage-cost sewing history to tailoring because same-key counterparts exist; normalize only route flags/material tables, transfer employee M2M with ON CONFLICT, archive exact resources by XMLID, and unlink only module-owned sewing UI records.

## Source Nodes

- FurnitureMrpProductionOrder
- FurnitureMrpStageMixin