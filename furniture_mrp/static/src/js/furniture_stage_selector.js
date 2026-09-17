/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const STAGES = [
    { field: "use_priming", label: _t("التقديم"), icon: "fa fa-paint-brush" },
    { field: "use_painting", label: _t("تصنيع دهانات"), icon: "fa fa-tint" },
    { field: "use_carpentry", label: _t("تجميع"), icon: "fa fa-cubes" },
    { field: "use_bases", label: _t("القواعد"), icon: "fa fa-th-large" },
    { field: "use_finishing", label: _t("تجهيز"), icon: "fa fa-wrench" },
    { field: "use_tailoring", label: _t("تفصيل"), icon: "fa fa-cut" },
    { field: "use_upholstery", label: _t("كسوه"), icon: "fa fa-cube" },
    { field: "use_packaging", label: _t("التغليف"), icon: "fa fa-archive" },
];

export class FurnitureStageSelector extends Component {
    static template = "furniture_mrp.FurnitureStageSelector";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.stages = STAGES;
        this.stageState = useState({
            values: {},
            allowed: {},
            reasons: {},
            orderIncluded: true,
            orderSelectable: true,
        });
        useRecordObserver((record) => {
            this.stageState.values = Object.fromEntries(
                this.stages.map((stage) => [
                    stage.field,
                    Boolean(record.data[stage.field]),
                ])
            );
            this.stageState.allowed = Object.fromEntries(
                this.stages.map((stage) => {
                    const canField = `can_${stage.field}`;
                    return [
                        stage.field,
                        Object.hasOwn(record.data, canField)
                            ? Boolean(record.data[canField])
                            : true,
                    ];
                })
            );
            this.stageState.reasons = Object.fromEntries(
                this.stages.map((stage) => [
                    stage.field,
                    record.data[`reason_${stage.field}`] || "",
                ])
            );
            this.stageState.orderIncluded = !this.isBatchReleaseLine ||
                Boolean(record.data.include_in_request);
            this.stageState.orderSelectable = !this.isBatchReleaseLine ||
                !Object.hasOwn(record.data, "has_selectable_stages") ||
                Boolean(record.data.has_selectable_stages);
        });
    }

    get isBatchReleaseLine() {
        return this.props.record.resModel ===
            "furniture.mrp.advance.material.batch.wizard.line";
    }

    get isOrderIncluded() {
        return Boolean(this.stageState.orderIncluded);
    }

    get isOrderSelectable() {
        return Boolean(this.stageState.orderSelectable);
    }

    isSelected(stage) {
        return (
            (!this.isBatchReleaseLine || this.isOrderIncluded) &&
            Boolean(this.stageState.values[stage.field])
        );
    }

    get selectedCount() {
        return this.stages.filter((stage) => this.isSelected(stage)).length;
    }

    get availableStages() {
        return this.stages.filter(
            (stage) => Boolean(this.stageState.allowed[stage.field])
        );
    }

    get allAvailableStagesSelected() {
        return (
            this.isOrderIncluded &&
            this.availableStages.length > 0 &&
            this.availableStages.every((stage) => this.isSelected(stage))
        );
    }

    isDisabled(stage) {
        // Fields rendered inside an x2many kanban are marked readonly by the
        // view even while their parent wizard is being edited.  Batch cards
        // intentionally update their virtual line record in place, so only
        // the per-stage availability flag should lock them.
        return (
            (this.props.readonly && !this.isBatchReleaseLine) ||
            (this.isBatchReleaseLine && !this.isOrderIncluded) ||
            !this.stageState.allowed[stage.field]
        );
    }

    stageReason(stage) {
        return this.stageState.reasons[stage.field] || "";
    }

    toggleStage(stage) {
        if (this.isDisabled(stage)) {
            return;
        }
        return this.props.record.update({
            [stage.field]: !this.isSelected(stage),
        });
    }

    toggleOrder() {
        if (!this.isBatchReleaseLine || !this.isOrderSelectable) {
            return;
        }
        const includeOrder = !this.isOrderIncluded;
        const values = { include_in_request: includeOrder };
        if (!includeOrder) {
            for (const stage of this.stages) {
                values[stage.field] = false;
            }
        }
        return this.props.record.update(values);
    }

    selectAllAvailableStages() {
        if (
            !this.isBatchReleaseLine ||
            !this.isOrderSelectable ||
            this.allAvailableStagesSelected
        ) {
            return;
        }
        const values = { include_in_request: true };
        for (const stage of this.stages) {
            // The availability flag is the single source of truth.  Besides
            // selecting every usable stage, assigning false here removes any
            // stale selection from a stage that became blocked meanwhile.
            values[stage.field] = Boolean(
                this.stageState.allowed[stage.field]
            );
        }
        return this.props.record.update(values);
    }
}

registry.category("fields").add("furniture_stage_selector", {
    component: FurnitureStageSelector,
    displayName: _t("كروت مراحل التصنيع"),
    supportedTypes: ["boolean"],
    isEmpty: () => false,
});

/** Touch-first selector used by the x2many material-check cards. */
export class FurnitureBatchMaterialCheckSelector extends Component {
    static template = "furniture_mrp.FurnitureBatchMaterialCheckSelector";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.selectionState = useState({ selected: false });
        useRecordObserver((record) => {
            this.selectionState.selected = Boolean(
                record.data[this.props.name]
            );
        });
    }

    get isSelected() {
        return this.selectionState.selected;
    }

    toggleSelection() {
        return this.props.record.update({
            [this.props.name]: !this.isSelected,
        });
    }
}

registry.category("fields").add(
    "furniture_batch_material_check_selector",
    {
        component: FurnitureBatchMaterialCheckSelector,
        displayName: _t("اختيار أمر لفحص المواد"),
        supportedTypes: ["boolean"],
        isEmpty: () => false,
    }
);

/**
 * Parent-level selector that keeps the boolean toggle and every virtual
 * x2many kanban line in sync.  A regular server onchange is not sufficient
 * here because the unsaved line records live in the browser's relational
 * model until the wizard is submitted.
 */
export class FurnitureBatchMaterialCheckSelectAll extends Component {
    static template = "furniture_mrp.FurnitureBatchMaterialCheckSelectAll";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.selectAllState = useState({
            allSelected: false,
            busy: false,
            lineCount: 0,
            selectedCount: 0,
        });
        useRecordObserver((record) => this.syncSelectionState(record));
    }

    lineRecords(record = this.props.record) {
        return record.data.line_ids?.records || [];
    }

    syncSelectionState(record = this.props.record) {
        const records = this.lineRecords(record);
        const selectedCount = records.filter(
            (lineRecord) => Boolean(lineRecord.data.selected)
        ).length;
        this.selectAllState.lineCount = records.length;
        this.selectAllState.selectedCount = selectedCount;
        this.selectAllState.allSelected =
            records.length > 0 && selectedCount === records.length;
    }

    get allSelected() {
        return this.selectAllState.allSelected;
    }

    get isDisabled() {
        return this.selectAllState.busy || !this.selectAllState.lineCount;
    }

    async toggleAll() {
        if (this.isDisabled) {
            return;
        }
        const shouldSelect = !this.allSelected;
        this.selectAllState.busy = true;
        try {
            // Keep the transient-model field correct for server-side onchange
            // and for saving, then explicitly update the live x2many records
            // so every card and the selected counter refresh immediately.
            await this.props.record.update({
                [this.props.name]: shouldSelect,
            });
            for (const lineRecord of this.lineRecords()) {
                if (Boolean(lineRecord.data.selected) !== shouldSelect) {
                    await lineRecord.update({ selected: shouldSelect });
                }
            }
            this.syncSelectionState();
        } finally {
            this.selectAllState.busy = false;
        }
    }
}

registry.category("fields").add(
    "furniture_batch_material_check_select_all",
    {
        component: FurnitureBatchMaterialCheckSelectAll,
        displayName: _t("تحديد كل أوامر فحص المواد"),
        supportedTypes: ["boolean"],
        isEmpty: () => false,
    }
);
