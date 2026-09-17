/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { FurnitureMrpStageDashboardPage } from "@furniture_mrp/js/mrp_stage_dashboard";

patch(FurnitureMrpStageDashboardPage.prototype, {
    get needPlannedOrderCount() {
        if (this.orderSupervisorOrders.some(order => order.textile_kit)) {
            return new Set(this.orderSupervisorOrders.flatMap(order =>
                order._textile_source_order_ids || [order.id])).size;
        }
        return super.needPlannedOrderCount;
    },
    async _operateTextileKit(order, operation, extra = {}) {
        return this._runOrderSupervisorAction(order, operation, () => this.orm.call(
            "furniture.mrp.production", "action_textile_kit_operation", [], {
                token: order.textile_kit_token,
                stage_code: this.selectedStageCode,
                operation,
                date_from: this.stageDashboard.appliedDateFrom || false,
                date_to: this.stageDashboard.appliedDateTo || false,
                context: this.context,
                ...extra,
            }));
    },
    async requestOrderStageMaterials(order) {
        if (order?.textile_kit) return this._operateTextileKit(order, "materials");
        return super.requestOrderStageMaterials(...arguments);
    },
    async startOrderAllProducts(order) {
        if (order?.textile_kit) return this._operateTextileKit(order, "start");
        if (order?.textile_loose) return this._operateTextileLoose(order, "start");
        return super.startOrderAllProducts(...arguments);
    },
    async finishOrderStage(order) {
        if (order?.textile_kit) return this._operateTextileKit(order, "finish");
        if (order?.textile_loose) return this._operateTextileLoose(order, "finish");
        return super.finishOrderStage(...arguments);
    },
    async _operateTextileLoose(order, operation) {
        const field = operation === "start" ? "startable_production_line_ids" : "quality_production_line_ids";
        const ids = [...new Set(order.product_lines.flatMap(product => product[field] || []))];
        return this._runOrderSupervisorAction(order, operation, () => this.orm.call(
            "furniture.mrp.production", "action_textile_loose_operation", [[order.id]], {
                stage_code: this.selectedStageCode, operation, line_ids: ids, context: this.context,
            }));
    },
    async pauseOrderTimer(order) {
        if (order?.textile_kit) return this._operateTextileKit(order, "pause");
        return super.pauseOrderTimer(...arguments);
    },
    async resumeOrderTimer(order) {
        if (order?.textile_kit) return this._operateTextileKit(order, "resume");
        return super.resumeOrderTimer(...arguments);
    },
    async reviewOrderProductQuality(order, product, decision) {
        if (order?.textile_kit) return this._operateTextileKit(order, "quality", {
            line_ids: product.quality_production_line_ids, decision,
        });
        return super.reviewOrderProductQuality(...arguments);
    },
    async openOrderProductBom(order, product) {
        if (order?.textile_kit) return super.openOrderProductBom(
            {...order, id: product._textile_source_order_id}, product);
        return super.openOrderProductBom(...arguments);
    },
    get orderSupervisorSummaryMetrics() {
        const metrics = super.orderSupervisorSummaryMetrics;
        if (this.orderSupervisorOrders.some(order => order.textile_kit)) {
            return metrics.map(metric => metric.key === "orders"
                ? {...metric, label: "أوامر الإنتاج"} : metric);
        }
        return metrics;
    },
});
