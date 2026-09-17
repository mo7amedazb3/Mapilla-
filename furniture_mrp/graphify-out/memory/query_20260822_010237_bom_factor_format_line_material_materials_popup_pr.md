---
type: "query"
date: "2026-08-22T01:02:37.612373+00:00"
question: "bom factor format line material materials popup product production quantities quantity"
contributor: "graphify"
outcome: "dead_end"
---

# Q: bom factor format line material materials popup product production quantities quantity

## Answer

The graph query returned generic FurnitureMrpProduction quantity methods and did not locate the exact UI fields. Direct source inspection found product_qty and dimension_factor in views/mrp_production_view.xml and product_qty plus eight qty_needed fields in views/mrp_production_line_bom_popup_view.xml. Odoo core FloatField supports options min_display_digits, which trims unnecessary trailing zeros while keeping one decimal and retaining each field's configured maximum precision.

## Outcome

- Signal: dead_end