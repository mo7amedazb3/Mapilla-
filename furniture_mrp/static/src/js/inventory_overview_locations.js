/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import {
    StockKanbanRenderer,
    StockKanbanView,
} from "@stock/components/stock_overview/stock_overview";
import { onWillDestroy, onWillStart, useState } from "@odoo/owl";


export class FurnitureInventoryOverviewRenderer extends StockKanbanRenderer {
    static template = "furniture_mrp.InventoryOverviewRenderer";

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.busService = useService("bus_service");
        this.dashboardState = useState({
            loaded: false,
            error: false,
            locations: [],
            pending: {
                total: 0,
                regular_count: 0,
                advance_count: 0,
            },
        });
        this.onStoreNotification = () => this.loadFurnitureOverview();
        this.busService.subscribe(
            "furniture_store_notification",
            this.onStoreNotification
        );
        onWillStart(() => this.loadFurnitureOverview());
        onWillDestroy(() => {
            this.busService.unsubscribe(
                "furniture_store_notification",
                this.onStoreNotification
            );
        });
    }

    async loadFurnitureOverview() {
        try {
            const data = await this.orm.call(
                "stock.location",
                "furniture_inventory_overview_data",
                []
            );
            this.dashboardState.locations = data.locations || [];
            this.dashboardState.pending = data.pending || {
                total: 0,
                regular_count: 0,
                advance_count: 0,
            };
            this.dashboardState.error = false;
        } catch {
            this.dashboardState.error = true;
        } finally {
            this.dashboardState.loaded = true;
        }
    }

    async openInventoryLocation(location) {
        const action = await this.orm.call(
            "stock.location",
            "furniture_open_inventory_location",
            [location.location_id]
        );
        return this.actionService.doAction(action);
    }

    onInventoryLocationKeydown(event, location) {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            this.openInventoryLocation(location);
        }
    }

    async openPendingRequests(requestKind) {
        const action = await this.orm.call(
            "stock.location",
            "furniture_open_pending_store_requests",
            [requestKind]
        );
        return this.actionService.doAction(action);
    }
}

export const FurnitureStockKanbanView = {
    ...StockKanbanView,
    Renderer: FurnitureInventoryOverviewRenderer,
};

registry.category("views").add(
    "furniture_stock_dashboard_kanban",
    FurnitureStockKanbanView
);
