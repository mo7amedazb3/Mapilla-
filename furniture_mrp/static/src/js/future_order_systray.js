/** @odoo-module **/

import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillUnmount, useState } from "@odoo/owl";


export class FurnitureFutureOrderSystray extends Component {
    static template = "furniture_mrp.FutureOrderSystray";
    static components = { Dropdown };
    static props = [];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.busService = useService("bus_service");
        this.busService.start();
        this.dropdown = useDropdownState();
        this.state = useState({
            canManage: false,
            loading: true,
            pendingTotal: 0,
            dueSoonCount: 0,
            orders: [],
            nextPending: false,
            error: false,
        });
        this._refreshPromise = false;
        this._refreshQueued = false;
        this._refreshTimer = false;
        this._retryTimer = false;
        this._destroyed = false;
        this._onFutureOrderChanged = () => this.refresh();
        this.busService.subscribe(
            "furniture_future_order_changed",
            this._onFutureOrderChanged
        );
        this.refresh();
        onWillUnmount(() => {
            this._destroyed = true;
            if (this._refreshTimer) {
                browser.clearInterval(this._refreshTimer);
            }
            if (this._retryTimer) {
                browser.clearTimeout(this._retryTimer);
            }
            this.busService.unsubscribe(
                "furniture_future_order_changed",
                this._onFutureOrderChanged
            );
        });
    }

    async refresh() {
        if (this._refreshPromise) {
            this._refreshQueued = true;
            return this._refreshPromise;
        }
        this._refreshPromise = this.orm.call(
            "furniture.mrp.future.order",
            "get_alert_data",
            [],
            { limit: 20 }
        );
        try {
            const data = await this._refreshPromise;
            if (this._destroyed) {
                return;
            }
            if (this._retryTimer) {
                browser.clearTimeout(this._retryTimer);
                this._retryTimer = false;
            }
            this.state.canManage = Boolean(data.can_manage);
            this.state.pendingTotal = Number(data.pending_total || 0);
            this.state.dueSoonCount = Number(data.due_soon_count || 0);
            this.state.orders = data.orders || [];
            this.state.nextPending = data.next_pending || false;
            this.state.error = false;
            if (this.state.canManage && !this._refreshTimer) {
                this._refreshTimer = browser.setInterval(
                    () => this.refresh(),
                    60000
                );
            } else if (!this.state.canManage && this._refreshTimer) {
                browser.clearInterval(this._refreshTimer);
                this._refreshTimer = false;
            }
        } catch {
            if (!this._destroyed) {
                this.state.error = true;
                if (!this._retryTimer) {
                    this._retryTimer = browser.setTimeout(() => {
                        this._retryTimer = false;
                        if (!this._destroyed) {
                            this.refresh();
                        }
                    }, 30000);
                }
            }
        } finally {
            if (!this._destroyed) {
                this.state.loading = false;
            }
            this._refreshPromise = false;
            if (this._refreshQueued && !this._destroyed) {
                this._refreshQueued = false;
                void this.refresh();
            }
        }
    }

    async openOrder(orderId) {
        this.dropdown.close();
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "طلب تسليم قادم",
            res_model: "furniture.mrp.future.order",
            res_id: Number(orderId),
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openPendingOrders() {
        this.dropdown.close();
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "طلبات التسليم بانتظار القرار",
            res_model: "furniture.mrp.future.order",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", "pending"]],
            context: { search_default_pending: 1 },
            target: "current",
        });
    }

    async createOrder() {
        this.dropdown.close();
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "طلب تسليم جديد",
            res_model: "furniture.mrp.future.order",
            views: [[false, "form"]],
            target: "current",
        });
    }
}


registry.category("systray").add(
    "furniture_mrp.future_order_systray",
    { Component: FurnitureFutureOrderSystray },
    { sequence: 102 }
);
