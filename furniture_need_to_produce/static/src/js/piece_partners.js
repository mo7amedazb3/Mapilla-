/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

export class PiecePartnersDialog extends Component {
    static template = "furniture_need_to_produce.PiecePartnersDialog";
    static components = { Dialog, Many2XAutocomplete };
    static props = { close: Function, record: Object };

    setup() {
        this.orm = useService("orm");
        const values = this.props.record.data.custom_partners || {};
        this.companyId = values.company_id;
        this.state = useState({
            buyer: values.buyer_partner_id || false,
            beneficiary: values.beneficiary_partner_id || false,
            saving: false, error: "",
        });
        this.activeActions = { create: false, createEdit: false, write: false };
    }

    partnerDomain(buyer = false) {
        const domain = [["active", "=", true], ["company_id", "in", [false, this.companyId]]];
        if (buyer) domain.push(["is_company", "=", true]);
        return domain;
    }

    selectPartner(field, records) {
        if (this.state.saving) return;
        const record = records && records[0];
        this.state[field] = record ? [record.id, record.display_name || record.name] : false;
    }

    async save() {
        if (this.state.saving) return;
        this.state.saving = true;
        this.state.error = "";
        try {
            await this.orm.call("furniture.need.to.produce", "action_save_custom_partners",
                [[this.props.record.resId], this.state.buyer?.[0] || false,
                    this.state.beneficiary?.[0] || false], { context: this.props.record.context });
            await this.props.record.load();
            this.props.close();
        } catch (error) {
            this.state.error = error.data?.message || "تعذر حفظ الاختيار. حاول مرة أخرى.";
        } finally {
            this.state.saving = false;
        }
    }
}

export class PiecePartners extends Component {
    static template = "furniture_need_to_produce.PiecePartners";
    static props = { ...standardFieldProps };

    setup() {
        this.dialog = useService("dialog");
        this.bulk = useState(this.env.ntpPipelineBulk || {});
        this.state = useState({ canEdit: false });
        onWillStart(async () => {
            this.state.canEdit = await user.hasGroup("furniture_mrp.group_furniture_mrp_manager");
        });
    }

    get editable() {
        return this.props.record.data.is_custom && this.props.record.data.state === "draft" &&
            this.state.canEdit && !this.bulk.busy && !this.bulk.confirming;
    }

    open() {
        if (!this.editable) return;
        this.dialog.add(PiecePartnersDialog, { record: this.props.record });
    }
}

registry.category("fields").add("need_produce_partners", {
    component: PiecePartners, supportedTypes: ["json"],
});
