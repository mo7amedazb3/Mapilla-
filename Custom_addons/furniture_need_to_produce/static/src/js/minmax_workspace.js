/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { StageReplenishmentDashboard } from "@furniture_stage_replenishment/js/stage_replenishment_dashboard";
import { FinalReplenishmentDashboard } from "@furniture_stage_replenishment/js/final_replenishment_dashboard";
import { stageShortageAction } from "./stage_shortages";

// Presentation only. This opt-in addon keeps the existing limit/save/run logic.
const DISPLAY_ORDER = ["carpentry", "bases", "finishing", "tailoring", "painting", "final"];

patch(StageReplenishmentDashboard.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.ntpModel = "";
    },

    get ntpModelOptions() {
        return [...new Set(this.state.rows.map((row) => row.model).filter(Boolean))]
            .sort((left, right) => left.localeCompare(right, "ar"));
    },

    get filteredRows() {
        return super.filteredRows.filter((row) => this.ntpMatchesModel(row));
    },

    ntpMatchesModel(row) {
        return !this.state.ntpModel || row.model === this.state.ntpModel;
    },

    setNtpModel(event) {
        if (!this.guardUnsavedChanges()) {
            event.target.value = this.state.ntpModel;
            return;
        }
        this.state.ntpModel = event.target.value;
        this.state.selectedIds = {};
    },

    applyData() {
        super.applyData(...arguments);
        if (this.state.ntpModel && !this.ntpModelOptions.includes(this.state.ntpModel)) {
            this.state.ntpModel = "";
        }
    },

    get ntpDisplayStages() {
        const rank = (code) => {
            const index = DISPLAY_ORDER.indexOf(code);
            return index === -1 ? DISPLAY_ORDER.length : index;
        };
        return [...this.state.stages].sort((a, b) => rank(a.code) - rank(b.code));
    },

    async openNeedToProduce() {
        if (!this.guardUnsavedChanges()) {
            return;
        }
        return this.action.doAction(this.isFinalStage
            ? "furniture_need_to_produce.action_need_to_produce"
            : stageShortageAction(this.state.activeStage) || "furniture_need_to_produce.action_stage_shortages_frame");
    },
});

// Filter the already-authorized payload locally: no reload, stock mutation,
// or discarded unsaved limits when switching between models.
patch(FinalReplenishmentDashboard.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.ntpFinalModel = "";
    },

    get ntpFinalModelOptions() {
        return [...new Set(this.state.rows.map((row) => row.model).filter(Boolean))]
            .sort((left, right) => left.localeCompare(right, "ar"));
    },

    get filteredRows() {
        return super.filteredRows.filter((row) =>
            !this.state.ntpFinalModel || row.model === this.state.ntpFinalModel
        );
    },

    setNtpFinalModel(event) {
        this.state.ntpFinalModel = event.target.value;
    },

    clearNtpFinalModel() {
        this.state.ntpFinalModel = "";
    },

    applyData() {
        super.applyData(...arguments);
        if (this.state.ntpFinalModel && !this.ntpFinalModelOptions.includes(this.state.ntpFinalModel)) {
            this.state.ntpFinalModel = "";
        }
    },
});
