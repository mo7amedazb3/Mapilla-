---
type: "query"
date: "2026-08-20T04:30:54.382817+00:00"
question: "Static-review the new MPS schedule models, views, security, manifest and legacy MPS compatibility for Odoo 18"
contributor: "graphify"
outcome: "dead_end"
---

# Q: Static-review the new MPS schedule models, views, security, manifest and legacy MPS compatibility for Odoo 18

## Answer

The graph was stale and contained only older dashboard-oriented nodes, so it could not substantiate the new MPS implementation. Direct source inspection found and drove fixes for: model import order registry failure, supervisor sudo environment, personal worker record rules, public sudo action authorization, inaccessible parent-menu action, explicit clearing of the legacy parent action, invalid/incomplete inline operation creation, editable calendar inconsistency, and missing assignment rec_name. Legacy furniture.mrp.mps models/actions remain for sale-shortage links while the visible menu now points to worker scheduling.

## Outcome

- Signal: dead_end