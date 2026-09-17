/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class CentralPurchaseDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            isLoading: true,
            search: "",
            status: "need_reorder",
            rows: [],
            summary: {
                total_items: 0,
                need_reorder: 0,
                stock_ok: 0,
                suppliers: 0,
            },
            selectedIds: {},
            savingRows: {},
            preparingRows: {},
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    get filteredRows() {
        const search = this.state.search.trim().toLowerCase();
        return this.state.rows.filter((row) => {
            const matchesStatus = this.state.status === "all"
                || row.status === this.state.status
                || (this.state.status === "need_reorder" && this.isNeedReorderStatus(row.status));
            const matchesSearch = !search
                || row.product.toLowerCase().includes(search)
                || row.supplier.toLowerCase().includes(search);
            return matchesStatus && matchesSearch;
        });
    }

    get selectedCount() {
        return Object.values(this.state.selectedIds).filter(Boolean).length;
    }

    get visibleSelectableRows() {
        return this.filteredRows.filter((row) => row.can_prepare);
    }

    get allVisibleSelected() {
        const rows = this.visibleSelectableRows;
        return rows.length > 0 && rows.every((row) => this.state.selectedIds[row.id]);
    }

    formatQty(value) {
        return Number(value || 0).toFixed(2);
    }

    isNeedReorderStatus(status) {
        return ["to_order", "partial_order", "no_supplier"].includes(status);
    }

    statusClass(row) {
        if (row.status === "ok") {
            return "o_spa_cp_badge_ok";
        }
        if (row.status === "ordered") {
            return "o_spa_cp_badge_ordered";
        }
        if (row.status === "no_supplier") {
            return "o_spa_cp_badge_danger";
        }
        return "o_spa_cp_badge_order";
    }

    qtyClass(value) {
        const qty = Number(value || 0);
        if (qty <= 0) {
            return "o_spa_cp_qty_ok";
        }
        if (qty >= 200) {
            return "o_spa_cp_qty_danger";
        }
        return "o_spa_cp_qty_warn";
    }

    async loadData() {
        this.state.isLoading = true;
        try {
            const data = await this.orm.call(
                "stock.warehouse.orderpoint",
                "spa_central_purchase_dashboard_data",
                []
            );
            this.state.rows = data.rows || [];
            this.state.summary = data.summary || this.state.summary;
            this.state.selectedIds = {};
        } catch (error) {
            console.error("Unable to load central purchase dashboard", error);
            this.notification.add("Could not load Central Purchase Replenishment.", {
                type: "danger",
            });
        } finally {
            this.state.isLoading = false;
        }
    }

    async refreshMaterials() {
        this.state.isLoading = true;
        try {
            await this.orm.call("stock.warehouse.orderpoint", "spa_sync_purchase_materials", []);
            await this.loadData();
            this.notification.add("Materials refreshed.", { type: "success" });
        } catch (error) {
            console.error("Unable to refresh materials", error);
            this.notification.add("Could not refresh materials.", { type: "danger" });
            this.state.isLoading = false;
        }
    }

    setStatus(status) {
        this.state.status = status;
    }

    updateSearch(event) {
        this.state.search = event.target.value;
    }

    toggleRow(row) {
        if (!row.can_prepare) {
            return;
        }
        this.state.selectedIds[row.id] = !this.state.selectedIds[row.id];
    }

    toggleVisibleRows() {
        const shouldSelect = !this.allVisibleSelected;
        for (const row of this.visibleSelectableRows) {
            this.state.selectedIds[row.id] = shouldSelect;
        }
    }

    setLimit(row, field, event) {
        row[field] = event.target.value;
    }

    async saveLimits(row) {
        this.state.savingRows[row.id] = true;
        try {
            const minimum = Number(row.minimum || 0);
            const maximum = Number(row.maximum || 0);
            const data = await this.orm.call(
                "stock.warehouse.orderpoint",
                "spa_update_central_purchase_limits",
                [row.id, minimum, maximum]
            );
            this.state.rows = data.rows || [];
            this.state.summary = data.summary || this.state.summary;
            this.notification.add("Minimum and maximum updated.", { type: "success" });
        } catch (error) {
            console.error("Unable to update limits", error);
            this.notification.add(error.message || "Could not update minimum/maximum.", {
                type: "danger",
            });
            await this.loadData();
        } finally {
            this.state.savingRows[row.id] = false;
        }
    }

    async prepareRfq(row) {
        await this.prepareIds([row.id]);
    }

    async prepareSelected() {
        const ids = Object.entries(this.state.selectedIds)
            .filter((entry) => entry[1])
            .map((entry) => Number(entry[0]));
        if (!ids.length) {
            this.notification.add("Select at least one item first.", { type: "warning" });
            return;
        }
        await this.prepareIds(ids);
    }

    async prepareIds(ids) {
        for (const id of ids) {
            this.state.preparingRows[id] = true;
        }
        try {
            const result = await this.orm.call(
                "stock.warehouse.orderpoint",
                "spa_prepare_central_purchase_rfq",
                [ids]
            );
            if (result) {
                await this.action.doAction(result);
            } else {
                this.notification.add("Purchase request created.", { type: "success" });
                await this.loadData();
            }
        } catch (error) {
            console.error("Unable to create purchase request", error);
            this.notification.add(error.message || "Could not create purchase request.", { type: "danger" });
        } finally {
            for (const id of ids) {
                this.state.preparingRows[id] = false;
            }
        }
    }

    openListView() {
        this.action.doAction("custom_spa_replenishment.action_spa_raw_material_replenishment_list");
    }
}

CentralPurchaseDashboard.template = "custom_spa_replenishment.CentralPurchaseDashboard";

registry.category("actions").add(
    "custom_spa_replenishment.central_purchase_dashboard",
    CentralPurchaseDashboard
);

