---
type: "query"
date: "2026-08-09T15:55:21.970296+00:00"
question: "Update furniture_mrp tests so sewing is an internal tailoring substage, not an independent route or material-only stage"
contributor: "graphify"
outcome: "corrected"
correction: "For route-removal test migrations, use graph traversal for orientation, then search all tests for the removed code and old stage counts and verify with an Odoo full-module test pass."
source_nodes: ["TestKitPlanner", "mrp_kit_planner.py"]
---

# Q: Update furniture_mrp tests so sewing is an internal tailoring substage, not an independent route or material-only stage

## Answer

Expanded from original query via graph vocabulary: [sewing, stage, selector, kit, material, planner]. The graph located TestKitPlanner and the stage-planner cluster, but it did not expose every stale nine-stage assertion. Direct rg plus a full Odoo test run identified test_material_only_stages, test_start_stage_selector, test_kit_planner, test_advance_material_release, and two 9-to-8 assertions in test_model_specific_recipes. Updated tests now treat sewing only as tailoring substage and 99 focused cases pass.

## Outcome

- Signal: corrected
- Correction: For route-removal test migrations, use graph traversal for orientation, then search all tests for the removed code and old stage counts and verify with an Odoo full-module test pass.

## Source Nodes

- TestKitPlanner
- mrp_kit_planner.py