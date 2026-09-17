---
type: "code_navigation"
date: "2026-07-31T23:58:07.167842+00:00"
question: "Simplify the stage dashboard product cards: remove the colored product-name box and remove duplicated planned and quality boxes from planned, working, completed, and remaining KPI tabs. Expanded vocabulary: dashboard, stage, product, group, planned, quality, quantity, completed, remaining, summary, template."
contributor: "graphify"
outcome: "useful"
---

# Q: Simplify the stage dashboard product cards: remove the colored product-name box and remove duplicated planned and quality boxes from planned, working, completed, and remaining KPI tabs. Expanded vocabulary: dashboard, stage, product, group, planned, quality, quantity, completed, remaining, summary, template.

## Answer

The shared UI is rendered by static/src/xml/mrp_stage_dashboard.xml and styled by static/src/css/mrp_stage_dashboard.css. The order-stage product title uses is-product-name; the non-order KPI cards use product_group_quantities. Restyle is-product-name as plain accented text and keep only working, completed, and remaining boxes in product_group_quantities. Preserve planned/quality in the JS payload and workflow calculations.

## Outcome

- Signal: useful