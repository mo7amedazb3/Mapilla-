# Factory contextual help

Frontend-only optional addon: static explanatory copy becomes a
small accessible `!` button opening an Odoo dialog. The shortages welcome card
has its button at the physical top-left in both RTL and LTR layouts.

Explicit template/view extensions cover shortages, production routes, factory
dashboards, MPS guidance, stage/final Min/Max, delivery and assembly requests,
attendance and overtime. Dynamic statuses, amounts, uncertainty/error warnings,
confirmations and operational buttons remain visible and unchanged.

There is no DOM scanning or heuristic removal. Owl owns the buttons; one shared
event handler opens plain-text dialogs and prevents help clicks/keyboard
activation from selecting or opening the enclosing card. Native form/kanban
views use the `factory_context_help` view widget. No models, tables, scheduled
tasks or business RPCs are added. Existing field hover help remains intact.

All explicit help triggers share a compact, RTL-friendly dialog with a custom
header, close button, readable plain-text panel and confirmation button. Its
520px maximum width, mobile sizing and animation are scoped to
`o_factory_help_modal`; ordinary Odoo dialogs are unaffected. Long explanations
scroll inside the body while close controls remain visible. Reduced-motion
preferences disable the entrance animation. Native Dialog still owns focus,
Escape, stacking and restoring focus to the trigger.

Add a reviewed static tip with `widget name="factory_context_help" title="..."
message="..."` in a native view, or an explicit native button with
`o_factory_help_button`, `data-help-title`, `data-help-message`, an accessible
label and `aria-haspopup="dialog"` in an Owl template. Never put help buttons
inside another button. Never hide validation errors or live state as help.

Local event-isolation regression test:

    node factory_context_help/tests/test_context_help.cjs

Uninstalling this addon removes only its UI extensions. It does not remove or
modify manufacturing, inventory, partners, attendance or payroll records.
