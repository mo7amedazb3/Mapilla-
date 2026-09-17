/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FloatField, floatField } from "@web/views/fields/float/float_field";
import { formatFloat } from "@web/views/fields/formatters";

// Keep the field's precision and locale; omit only insignificant zeroes.
function formatQuantity(value, options = {}) {
    return formatFloat(value, { ...options, trailingZeros: false });
}
formatQuantity.extractOptions = formatFloat.extractOptions;

class CompactQuantityField extends FloatField {
    get formattedValue() {
        return formatQuantity(this.value, {
            digits: this.props.digits,
            field: this.props.record.fields[this.props.name],
        });
    }
}

registry.category("fields").add("furniture_compact_quantity", {
    ...floatField,
    component: CompactQuantityField,
});
registry.category("formatters").add("furniture_compact_quantity", formatQuantity);
