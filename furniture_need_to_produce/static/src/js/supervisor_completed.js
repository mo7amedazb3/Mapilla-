/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { FurnitureMrpStageDashboardPage, groupStageRows } from "@furniture_mrp/js/mrp_stage_dashboard";

const quantity = (value) => Number(value) || 0;
const fullyCompleted = (row) => quantity(row.planned_qty) > 0 &&
    quantity(row.completed_qty) >= quantity(row.planned_qty) - 0.000001;

patch(FurnitureMrpStageDashboardPage.prototype, {
    setup() {
        super.setup(...arguments);
        this.needSupervisor = useState({
            section: "planned",
            completed: [],
            waiting: [],
            completedQuantity: 0,
            plannedOrderCount: 0,
            loading: false,
            error: "",
        });
    },

    get supervisorProductBatches() {
        return groupStageRows(super.supervisorProductBatches.filter((batch) => batch.state !== "done"));
    },

    get orderSupervisorOrders() {
        return groupStageRows(super.orderSupervisorOrders.filter((order) => !fullyCompleted(order)),
            (order) => ({...order.product_lines?.[0], model_id: order.model_id, model_name: order.model_name}));
    },

    get needPlannedOrderCount() {
        return this.isBatchSupervisorMode ? this.needSupervisor.plannedOrderCount : this.orderSupervisorOrders.length;
    },

    get needPlannedQuantity() {
        if (this.isBatchSupervisorMode) {
            return this.supervisorProductBatches.reduce(
                (total, batch) => total + quantity(batch.display_qty), 0,
            ) + this.needSupervisor.waiting.reduce(
                (total, row) => total + quantity(row.quantity), 0,
            );
        }
        return this.orderSupervisorOrders.reduce((total, order) => total + Math.max(
            0, quantity(order.planned_qty) - quantity(order.completed_qty),
        ), 0);
    },

    selectNeedSupervisorSection(section) {
        this.needSupervisor.section = section === "completed" ? "completed" : "planned";
        this.clearSupervisorOrderSelection();
    },

    async _fetchStageDashboard(stageCode, options = {}) {
        const previousStage = this.stageDashboard.selectedStageCode;
        const result = await super._fetchStageDashboard(stageCode, options);
        if (!result || !this.stageDashboard.supervisorMode) {
            return result;
        }
        const stage = this.stageDashboard.selectedStageCode;
        const request = this._stageDashboardRequest;
        if (stage !== previousStage) {
            this.needSupervisor.section = "planned";
        }
        this.needSupervisor.loading = true;
        this.needSupervisor.error = "";
        // Never display the previous stage's history while a new RPC is pending.
        this.needSupervisor.completed = [];
        this.needSupervisor.waiting = [];
        this.needSupervisor.completedQuantity = 0;
        this.needSupervisor.plannedOrderCount = 0;
        try {
            const sections = await this.orm.call(
                "furniture.mrp.production", "get_need_supervisor_stage_sections", [], {
                    stage_code: stage,
                    date_from: this.stageDashboard.appliedDateFrom || false,
                    date_to: this.stageDashboard.appliedDateTo || false,
                    context: this.context,
                },
            );
            if (request !== this._stageDashboardRequest || stage !== this.stageDashboard.selectedStageCode) {
                return result;
            }
            this.needSupervisor.completed = groupStageRows(sections.completed || []);
            this.needSupervisor.waiting = groupStageRows(sections.waiting || []);
            this.needSupervisor.completedQuantity = quantity(sections.completed_quantity);
            this.needSupervisor.plannedOrderCount = quantity(sections.planned_order_count);
        } catch (error) {
            if (request === this._stageDashboardRequest) {
                this.needSupervisor.error = _t("تعذر تحميل المكتمل والعمل المنتظر. اضغط تحديث للمحاولة مجددًا.");
            }
        } finally {
            if (request === this._stageDashboardRequest) {
                this.needSupervisor.loading = false;
            }
        }
        return result;
    },
});
