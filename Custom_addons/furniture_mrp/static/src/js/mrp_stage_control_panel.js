import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";

const FURNITURE_PRODUCTION_MODEL = "furniture.mrp.production";

const FURNITURE_STAGE_MODELS = new Set([
    "furniture.mrp.priming",
    "furniture.mrp.painting",
    "furniture.mrp.carpentry",
    "furniture.mrp.bases",
    "furniture.mrp.finishing",
    "furniture.mrp.tailoring",
    "furniture.mrp.upholstery",
    "furniture.mrp.packaging",
]);

const FURNITURE_NAVIGATION_MODELS = new Set([
    FURNITURE_PRODUCTION_MODEL,
    ...FURNITURE_STAGE_MODELS,
]);

function isFurnitureMrpModel(resModel) {
    return Boolean(resModel && resModel.startsWith("furniture.mrp."));
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        if (isFurnitureMrpModel(this.props.resModel)) {
            this.env.config.noBreadcrumbs = true;
        }
    },

    get isFurnitureMrpNavigationForm() {
        return FURNITURE_NAVIGATION_MODELS.has(this.props.resModel);
    },

    get isFurnitureProductionOrder() {
        return this.props.resModel === FURNITURE_PRODUCTION_MODEL;
    },

    get furnitureProductionOrderName() {
        if (!this.isFurnitureProductionOrder) {
            return "";
        }
        return this.model?.root?.data?.name || "";
    },

    async openFurnitureProductionOrder() {
        await this._openFurnitureStageNavigationAction("action_back_to_production_order");
    },

    async openFurnitureMrpDashboard() {
        if (this.props.resModel === FURNITURE_PRODUCTION_MODEL) {
            await this.actionService.doAction(
                "furniture_mrp.action_furniture_mrp_stage_dashboard_page",
                { clearBreadcrumbs: true }
            );
            return;
        }
        await this._openFurnitureStageNavigationAction("action_open_mrp_dashboard");
    },

    async _openFurnitureStageNavigationAction(method) {
        const record = this.model.root;
        if (!FURNITURE_STAGE_MODELS.has(record.resModel) || !record.resId) {
            return;
        }
        const action = await this.orm.call(record.resModel, method, [[record.resId]]);
        await this.actionService.doAction(action);
    },
});

for (const Controller of [KanbanController, ListController]) {
    patch(Controller.prototype, {
        setup() {
            super.setup(...arguments);
            if (isFurnitureMrpModel(this.props.resModel)) {
                this.env.config.noBreadcrumbs = true;
            }
        },
    });
}
