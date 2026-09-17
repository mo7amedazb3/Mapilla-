---
type: "query"
date: "2026-08-22T01:14:22.514337+00:00"
question: "The BoM quantity list still shows 0.060000 after adding adaptive float display options"
contributor: "graphify"
outcome: "corrected"
correction: "For adaptive floats in Odoo 18 list views that can switch between readonly formatter and editable Field component, set both XML options: minDigits for the list formatter and min_display_digits for FloatField."
---

# Q: The BoM quantity list still shows 0.060000 after adding adaptive float display options

## Answer

Expanded via graph vocabulary: [display, field, format, list, material, quantity]. The graph traversal was unrelated, so direct Odoo 18 web source inspection found two rendering paths. Form/edit FloatField.extractProps reads options.min_display_digits, but readonly list getFormattedValue uses formatFloat.extractOptions, which reads options.minDigits. The original fix only set min_display_digits, so the popup header changed to 1.0 while readonly material list cells stayed fixed at six decimals. The corrected view sets both minDigits=1 and min_display_digits=1 on every relevant field; version 18.0.2.2.230 passed 15 tests on a yasser3 clone and live views contain both keys.

## Outcome

- Signal: corrected
- Correction: For adaptive floats in Odoo 18 list views that can switch between readonly formatter and editable Field component, set both XML options: minDigits for the list formatter and min_display_digits for FloatField.