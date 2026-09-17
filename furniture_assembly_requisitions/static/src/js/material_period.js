/** @odoo-module **/

import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { formatFloat } from "@web/views/fields/formatters";

export class MaterialPeriod extends Component {
    static template = "furniture_assembly_requisitions.MaterialPeriod";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.requestId = 0;
        this.state = useState({
            from: "", to: "", result: null, loading: false, stage: "carpentry",
        });
        onWillStart(() => this.loadRecord(this.props.record));
        onWillUpdateProps((props) => {
            if (props.record.resId !== this.recordId) {
                return this.loadRecord(props.record);
            }
        });
    }

    async loadRecord(record) {
        ++this.requestId;
        this.state.loading = false;
        this.recordId = record.resId;
        this.weekFrom = record.data.week_start?.toISODate() || "";
        this.weekTo = record.data.week_end?.toISODate() || "";
        this.state.from = this.weekFrom;
        this.state.to = this.weekTo;
        this.state.result = null;
        if (this.recordId) {
            await this.applyPeriod();
        }
    }

    async applyPeriod() {
        const { from, to } = this.state;
        if (!from || !to || from > to) {
            this.notification.add("اختار تاريخ بداية ونهاية صحيحين، والنهاية تكون بعد البداية أو نفس اليوم.", { type: "warning" });
            return;
        }
        const requestId = ++this.requestId;
        this.state.loading = true;
        try {
            const result = await this.orm.call(
                "furniture.assembly.weekly.material.report", "get_period_materials",
                [[this.recordId], from, to]
            );
            if (requestId === this.requestId) {
                this.state.result = result;
            }
        } catch (error) {
            if (requestId === this.requestId) {
                this.notification.add(error.data?.message || "تعذر تحميل الاستهلاك. جرّب مرة أخرى.", { type: "danger" });
            }
        } finally {
            if (requestId === this.requestId) {
                this.state.loading = false;
            }
        }
    }

    onPeriodChanged(field, event) {
        this.state[field] = event.target.value;
        if (this.recordId && this.state.from && this.state.to && this.state.from <= this.state.to) {
            return this.applyPeriod();
        }
    }

    resetPeriod() {
        this.state.from = this.weekFrom;
        this.state.to = this.weekTo;
        return this.applyPeriod();
    }

    get lines() {
        return this.state.result?.stages.find(stage => stage.code === this.state.stage)?.lines || [];
    }

    number(value) {
        return formatFloat(value, { digits: [16, 3] });
    }

    total(field) {
        return this.number(this.lines.reduce((sum, line) => sum + line[field], 0));
    }

    date(value) {
        return value.split("-").reverse().join("/");
    }
}

registry.category("fields").add("mapilla_material_period", {
    component: MaterialPeriod,
    supportedTypes: ["integer"],
});
