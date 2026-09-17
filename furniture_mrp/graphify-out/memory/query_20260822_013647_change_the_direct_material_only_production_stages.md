---
type: "implementation"
date: "2026-08-22T01:36:47.795056+00:00"
question: "Change the direct material-only production stages from tailoring and upholstery to tailoring and painting, while restoring upholstery as a physical WIP stage."
contributor: "graphify"
outcome: "useful"
---

# Q: Change the direct material-only production stages from tailoring and upholstery to tailoring and painting, while restoring upholstery as a physical WIP stage.

## Answer

The behavior is centralized in FURNITURE_MATERIAL_ONLY_STAGE_CODES in furniture_mrp/models/mrp_production_order.py. Replacing upholstery with painting automatically updates start eligibility, store-request kind, finished-product movement, stage transfer eligibility, and quality completion. Regression coverage in tests/test_material_only_stages.py verifies painting and tailoring can start from raw materials in parallel, never move the finished product, and upholstery remains transferable/physical and cannot start after priming begins until the product reaches its hall. Version 18.0.2.2.231 deployed to yasser3; 89 related tests passed.

## Outcome

- Signal: useful