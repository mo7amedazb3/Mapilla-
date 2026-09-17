---
type: "query"
date: "2026-08-13T22:34:34.034290+00:00"
question: "Inspect current tailoring setup and design the smallest Odoo 18 same-dialog inline material editor with no secondary modal."
contributor: "graphify"
outcome: "corrected"
correction: "Use direct models/mrp_tailoring_material_setup.py, views/mrp_tailoring_material_setup_view.xml, and CSS. Keep the editor top-level in the same form; reload the modal through a custom form-controller bus event, and delegate persistence to the existing guarded action_apply pipeline."
source_nodes: ["setup()"]
---

# Q: Inspect current tailoring setup and design the smallest Odoo 18 same-dialog inline material editor with no secondary modal.

## Answer

Expanded from graph vocabulary via [tailoring, material, setup, wizard, action, apply, line, product, uom, view, production]. Graph traversal was a dead end and surfaced only mrp_stage_control_panel setup(). Direct source inspection found the plus buttons return target=new from FurnitureMrpTailoringSetupWizardLine._open_material_editor. The safest minimal-JS design is one active top-level editor panel in the existing setup wizard, with per-kind apply, stored stale snapshot, and a module custom FormController plus client-action bus event that reloads only the current modal; soft_reload, target=new, and False are unsafe for this purpose.

## Outcome

- Signal: corrected
- Correction: Use direct models/mrp_tailoring_material_setup.py, views/mrp_tailoring_material_setup_view.xml, and CSS. Keep the editor top-level in the same form; reload the modal through a custom form-controller bus event, and delegate persistence to the existing guarded action_apply pipeline.

## Source Nodes

- setup()