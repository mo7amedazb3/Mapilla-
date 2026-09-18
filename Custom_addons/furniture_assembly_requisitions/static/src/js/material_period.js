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
            savingLineId: null,
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
                if (!result.stages.some((stage) => stage.code === this.state.stage)) {
                    this.state.stage = result.stages[0]?.code || "";
                }
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

    get isSupervisor() {
        return Boolean(this.state.result?.is_supervisor);
    }

    statusLabel(status) {
        return {
            shortage: "عجز",
            surplus: "فائض",
            balanced: "مطابق",
            pending: "—",
        }[status] || "—";
    }

    number(value) {
        return formatFloat(value, { digits: [16, 3] });
    }

    total(field) {
        return this.number(this.lines.reduce((sum, line) => sum + (Number(line[field]) || 0), 0));
    }

    totalCounted(field) {
        return this.number(this.lines.reduce(
            (sum, line) => sum + (line.counted ? (Number(line[field]) || 0) : 0),
            0
        ));
    }

    normalizeNumber(value) {
        const arabicDigits = "٠١٢٣٤٥٦٧٨٩";
        const persianDigits = "۰۱۲۳۴۵۶۷۸۹";
        return String(value)
            .trim()
            .replace(/[٠-٩]/g, (digit) => arabicDigits.indexOf(digit))
            .replace(/[۰-۹]/g, (digit) => persianDigits.indexOf(digit))
            .replace(/٬/g, "")
            .replace(/[٫,]/g, ".");
    }

    async saveActual(line, event) {
        const input = event.target;
        const normalized = this.normalizeNumber(input.value);
        const quantity = Number(normalized);
        if (normalized === "" || !Number.isFinite(quantity) || quantity < 0) {
            this.notification.add("أدخل رقمًا صحيحًا موجبًا أو صفرًا.", { type: "warning" });
            input.value = line.counted ? line.actual_qty : "";
            return;
        }
        this.state.savingLineId = line.id;
        input.disabled = true;
        try {
            const method = this.isSupervisor
                ? "save_actual_inventory"
                : "save_admin_actual_inventory";
            const saved = await this.orm.call(
                "furniture.assembly.weekly.material.report", method,
                [[this.recordId], this.state.stage, line.id, quantity, line.uom_id]
            );
            line.counted = saved.counted;
            line.actual_qty = saved.actual_qty;
            line.uom_id = saved.uom_id;
            line.uom_name = saved.uom_name;
            if ("status" in saved) {
                line.status = saved.status;
            }
            if ("variance_qty" in saved) {
                line.variance_qty = saved.variance_qty;
            }
            input.value = saved.actual_qty;
            this.notification.add(`تم حفظ جرد ${line.name}.`, { type: "success" });
        } catch (error) {
            input.value = line.counted ? line.actual_qty : "";
            this.notification.add(error.data?.message || "تعذر حفظ الجرد الفعلي.", { type: "danger" });
        } finally {
            input.disabled = false;
            this.state.savingLineId = null;
        }
    }

    async changeUom(line, event) {
        const previousUomId = line.uom_id;
        const uomId = Number(event.target.value);
        this.state.savingLineId = line.id;
        try {
            const saved = await this.orm.call(
                "furniture.assembly.weekly.material.report", "set_actual_inventory_uom",
                [[this.recordId], this.state.stage, line.id, uomId]
            );
            line.counted = saved.counted;
            line.actual_qty = saved.actual_qty;
            line.uom_id = saved.uom_id;
            line.uom_name = saved.uom_name;
            line.status = saved.status;
        } catch (error) {
            event.target.value = previousUomId;
            this.notification.add(error.data?.message || "تعذر تغيير وحدة القياس.", { type: "danger" });
        } finally {
            this.state.savingLineId = null;
        }
    }

    date(value) {
        return value.split("-").reverse().join("/");
    }
}

registry.category("fields").add("mapilla_material_period", {
    component: MaterialPeriod,
    supportedTypes: ["integer"],
});
