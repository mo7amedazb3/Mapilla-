/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const RULE_MODEL = "furniture.mrp.final.replenishment.rule";

const STATUS_LABELS = {
    disabled: "غير مُعدّ",
    to_produce: "يحتاج تصنيع",
    draft: "مغطى بمسودة",
    incoming: "جاري في المسار",
    working: "رصيد تحت التشغيل",
    ok: "المستوى سليم",
};

export class FinalReplenishmentDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.formatter = new Intl.NumberFormat("ar-EG", { maximumFractionDigits: 3 });
        this.state = useState({
            loading: true,
            syncing: false,
            runningAll: false,
            rows: [],
            summary: {},
            search: "",
            status: "all",
            dirty: {},
            saving: {},
            running: {},
        });
        onWillStart(() => this.load());
    }

    get filteredRows() {
        const search = this.state.search.trim().toLocaleLowerCase("ar");
        return this.state.rows.filter((row) => {
            if (this.state.status !== "all" && row.status !== this.state.status) {
                return false;
            }
            return !search || [row.product, row.model, row.recipe]
                .filter(Boolean).join(" ").toLocaleLowerCase("ar").includes(search);
        });
    }

    formatQty(value) {
        const number = Number(value || 0);
        return this.formatter.format(Number.isFinite(number) ? number : 0);
    }

    statusLabel(row) {
        return STATUS_LABELS[row.status] || row.status_label || "—";
    }

    statusClass(row) {
        return `o_ffr_status is-${row.status || "disabled"}`;
    }

    errorMessage(error, fallback) {
        return error?.data?.message || error?.message || fallback;
    }

    applyData(data) {
        this.state.rows = data?.rows || [];
        this.state.summary = data?.summary || {};
        this.state.dirty = {};
    }

    async load() {
        this.state.loading = true;
        try {
            this.applyData(await this.orm.call(
                RULE_MODEL, "final_replenishment_dashboard_data", []
            ));
        } catch (error) {
            this.notification.add(this.errorMessage(error, "تعذر تحميل مخزون المنتج النهائي."), {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    async sync() {
        this.state.syncing = true;
        try {
            this.applyData(await this.orm.call(
                RULE_MODEL, "final_replenishment_sync_rules", []
            ));
            this.notification.add("تم تحديث الأصناف من الريسيبي.", { type: "success" });
        } catch (error) {
            this.notification.add(this.errorMessage(error, "تعذر تحديث الأصناف."), {
                type: "danger",
            });
        } finally {
            this.state.syncing = false;
        }
    }

    setSearch(event) {
        this.state.search = event.target.value;
    }

    setStatus(event) {
        this.state.status = event.target.value;
    }

    setLimit(row, field, event) {
        row[field] = event.target.value;
        this.state.dirty[row.id] = true;
    }

    async save(row) {
        const minimum = Number(row.minimum);
        const maximum = Number(row.maximum);
        if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum < 0 || maximum < minimum) {
            this.notification.add("راجع Min وMax: لازم أرقام موجبة وMin لا يزيد عن Max.", {
                type: "warning",
            });
            return;
        }
        this.state.saving[row.id] = true;
        try {
            this.applyData(await this.orm.call(
                RULE_MODEL,
                "final_replenishment_update_limits",
                [row.id, minimum, maximum]
            ));
            this.notification.add(`تم حفظ حدود ${row.product} — ${row.model}.`, {
                type: "success",
            });
        } catch (error) {
            this.notification.add(this.errorMessage(error, "تعذر حفظ الحدود."), {
                type: "danger",
            });
        } finally {
            this.state.saving[row.id] = false;
        }
    }

    async run(row = null) {
        if (Object.values(this.state.dirty).some(Boolean)) {
            this.notification.add("احفظ قيم Min وMax المكتوبة أولًا.", { type: "warning" });
            return;
        }
        const ids = row ? [row.id] : null;
        if (row) {
            this.state.running[row.id] = true;
        } else {
            this.state.runningAll = true;
        }
        try {
            const result = await this.orm.call(
                RULE_MODEL, "final_replenishment_run_now", [ids]
            );
            this.applyData(result?.data);
            const count = Number(result?.created_count || 0);
            this.notification.add(
                count
                    ? `تم التجميع المتاح وتجهيز ${count} أمر تقديم/تجميع كمسودة.`
                    : "تم تجميع المتاح وتحديث الاحتياجات، ولا توجد مسودة جديدة.",
                { type: count ? "success" : "info" }
            );
            // Stay on the Min/Max dashboard after generation.  The dedicated
            // "Drafts"/"Orders" buttons open the results explicitly, while
            // the refreshed cards immediately show that the shortage is now
            // covered by drafts.
        } catch (error) {
            this.notification.add(this.errorMessage(error, "تعذر تشغيل التعويض."), {
                type: "danger",
            });
        } finally {
            if (row) {
                this.state.running[row.id] = false;
            } else {
                this.state.runningAll = false;
            }
        }
    }

    async openOrders(row = null) {
        const action = await this.orm.call(
            RULE_MODEL, "final_replenishment_open_orders", [row?.id || false]
        );
        if (action) {
            await this.action.doAction(action);
        }
    }
}

FinalReplenishmentDashboard.template =
    "furniture_stage_replenishment.FinalReplenishmentDashboard";

registry.category("actions").add(
    "furniture_stage_replenishment.final_replenishment_dashboard",
    FinalReplenishmentDashboard
);
