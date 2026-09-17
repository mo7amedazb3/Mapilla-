---
type: "query"
date: "2026-07-19T18:31:58.917366+00:00"
question: "Can cost_origin_line_id support a consolidated production-order display without changing Kit worker and quality behavior?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["FurnitureMrpProductionLine", "._split_for_partial_quantity()", "FurnitureMrpProductionOrder", "._prepare_kit_grouped_transfer_lines()"]
---

# Q: Can cost_origin_line_id support a consolidated production-order display without changing Kit worker and quality behavior?

## Answer

Expanded from original query via vocab: [production, line, kit, cost, origin, split, consolidate, display, view, quality, transfer, summary]. cost_origin_line_id groups Kit-materialized preserve-progress splits back to their technical root, so a read-only root-only summary with computed aggregate quantity is feasible for the locked Kit case. It is not an immutable universal original-line identity because first-stage partial splits use preserve_progress=False, and the root's Kit/customer metadata can be overwritten. A display-only summary does not improve quality approval performance because approval still iterates technical lines for stock output and stage costs.

## Outcome

- Signal: useful

## Source Nodes

- FurnitureMrpProductionLine
- ._split_for_partial_quantity()
- FurnitureMrpProductionOrder
- ._prepare_kit_grouped_transfer_lines()