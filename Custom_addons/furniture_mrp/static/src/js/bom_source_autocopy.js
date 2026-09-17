/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";

patch(Many2OneField.prototype, {
    async updateRecord(value) {
        const record = this.props.record;
        const result = await super.updateRecord(value);
        if (
            this.props.name === "furniture_source_bom_id" &&
            record.resModel === "mrp.bom" &&
            value &&
            value[0]
        ) {
            await record.save({ reload: false });
            const resId = record.resId;
            if (resId) {
                await this.orm.call("mrp.bom", "action_autocopy_source_bom", [[resId]]);
                await record.model.root.load();
            }
        }
        return result;
    },
});
