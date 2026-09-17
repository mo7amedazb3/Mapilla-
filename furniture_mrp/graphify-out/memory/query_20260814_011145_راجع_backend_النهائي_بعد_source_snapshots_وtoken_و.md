---
type: "query"
date: "2026-08-14T01:11:45.978024+00:00"
question: "راجع backend النهائي بعد source snapshots وtoken وPillow وبدون global material revision: ORM/RPC، locking، attachment/cache، واختبارات deploy الناقصة"
contributor: "graphify"
outcome: "corrected"
correction: "The existing graph is stale for this feature; audit the current mrp_tailoring_material_setup.py and tailoring_setup_inline_form.js directly until graphify --update is run."
source_nodes: ["FurnitureMrpProduction", "FurnitureMrpProductionLine"]
---

# Q: راجع backend النهائي بعد source snapshots وtoken وPillow وبدون global material revision: ORM/RPC، locking، attachment/cache، واختبارات deploy الناقصة

## Answer

Expanded from original query via prior graph vocabulary: [production, line, tailoring]. The graph snapshot predates the new direct-upload symbols, so current source was authoritative. Review found the record-method RPC signature correct, authorization/state checks before decoding, production/line locking plus post-lock snapshot/token checks, atomic attachment write and token rotation, and done/cancel cleanup without product-image mutation. No confirmed backend deploy blocker. Remaining gaps: no browser/HTTP RPC test, no multi-cursor concurrency test, no JPEG/pixel-bomb cases, possible rare lock-order deadlock with line-first structural paths, stale local preview after external replacement, full-size thumbnail bandwidth risk, and prior click-to-enlarge behavior is not wired in the current widget.

## Outcome

- Signal: corrected
- Correction: The existing graph is stale for this feature; audit the current mrp_tailoring_material_setup.py and tailoring_setup_inline_form.js directly until graphify --update is run.

## Source Nodes

- FurnitureMrpProduction
- FurnitureMrpProductionLine