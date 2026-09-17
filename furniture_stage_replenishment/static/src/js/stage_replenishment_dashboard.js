/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const RULE_MODEL = "furniture.mrp.stage.replenishment.rule";
const FINAL_RULE_MODEL = "furniture.mrp.final.replenishment.rule";

/** Presentation only: large sofa first, armchairs last, other pieces between. */
export function furniturePieceRank(name = "") {
    const value = String(name || "").normalize("NFKC").toLowerCase()
        .replace(/[\u064B-\u065F\u0670\u0640]/g, "").replace(/[أإآ]/g, "ا")
        .replace(/ة/g, "ه").replace(/ى/g, "ي");
    if (/(?:كنب|sofa|couch)/.test(value) && /(?:كبير|large|big|3[ -]?seater|three[ -]?seater)/.test(value)) return 0;
    if (/(?:فوتي|فوتيه|armchair|fauteuil)/.test(value)) return 2;
    return 1;
}

export function compareFurniturePieces(a, b) {
    return furniturePieceRank(a) - furniturePieceRank(b)
        || String(a || "").localeCompare(String(b || ""), "ar", {numeric: true});
}

// Bases and finishing share the finishing controller/card. Upholstery is
// production-driven and therefore has no Min/Max card.
const DISPLAY_STAGE_ORDER = [
    "carpentry",
    "bases",
    "finishing",
    "tailoring",
    "painting",
    "final",
];
const DISPLAY_STAGE_RANK = new Map(
    DISPLAY_STAGE_ORDER.map((stageCode, index) => [stageCode, index])
);

function stageDisplayRank(stageCode) {
    return DISPLAY_STAGE_RANK.get(stageCode) ?? DISPLAY_STAGE_ORDER.length;
}

const STATUS_LABELS = {
    disabled: "غير مُعدّ",
    to_produce: "يحتاج تصنيع",
    draft: "مسودة تغطي الاحتياج",
    incoming: "قادم للمرحلة",
    working: "داخل المرحلة",
    ok: "المستوى سليم",
};

const STATUS_ICONS = {
    disabled: "fa-sliders",
    to_produce: "fa-exclamation-triangle",
    draft: "fa-file-text-o",
    incoming: "fa-sign-in",
    working: "fa-cogs",
    ok: "fa-check-circle",
};

export class StageReplenishmentDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.quantityFormatter = new Intl.NumberFormat("en-US", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 3,
        });

        const actionContext = this.props.action?.context || {};
        const requestedStage = actionContext.stage_code || actionContext.default_stage_code;
        const initialStage = !requestedStage || requestedStage === "all" || requestedStage === "priming"
            ? "carpentry" : requestedStage;
        // Retain the two existing rule stores. Identical numeric IDs in the
        // stage and final models must never share edits, selection or RPCs.
        this.stageData = null;
        this.finalData = null;

        this.state = useState({
            isLoading: true,
            isSyncing: false,
            isRunningAll: false,
            search: "",
            status: "all",
            activeStage: initialStage,
            rows: [],
            stages: [],
            summary: {
                total_rules: 0,
                configured: 0,
                need_production: 0,
                draft_orders: 0,
            },
            selectedIds: {},
            dirtyRows: {},
            savingRows: {},
            runningRows: {},
        });

        onWillStart(async () => {
            await this.loadData(true);
        });
    }

    get filteredRows() {
        const search = this.state.search.trim().toLocaleLowerCase("ar");
        return this.state.rows.filter((row) => {
            const stageMatches = this.state.activeStage === "all"
                || row.stage_code === this.state.activeStage;
            const statusMatches = this.state.status === "all"
                || row.status === this.state.status;
            if (!stageMatches || !statusMatches) {
                return false;
            }
            if (!search) {
                return true;
            }
            const haystack = [
                row.product,
                row.model,
                row.recipe,
                row.stage,
                row.lane_label,
                row.last_production,
            ].filter(Boolean).join(" ").toLocaleLowerCase("ar");
            return haystack.includes(search);
        });
    }

    get modelGroups() {
        // Group the already-filtered, authorized rows without aggregating stock
        // across stages or changing the rule IDs used by the action buttons.
        const groups = new Map();
        const stages = this.ntpDisplayStages || this.state.stages;
        const ranks = new Map(stages.map((stage, index) => [stage.code, index]));
        for (const row of this.filteredRows) {
            const name = row.model || "بدون موديل";
            if (!groups.has(name)) {
                groups.set(name, { key: name, name, rows: [] });
            }
            groups.get(name).rows.push(row);
        }
        return [...groups.values()]
            .sort((a, b) => a.name.localeCompare(b.name, "ar", { numeric: true }))
            .map((group) => ({
                ...group,
                rows: group.rows.sort((a, b) =>
                    compareFurniturePieces(a.product, b.product)
                    || (ranks.get(a.stage_code) ?? stages.length) - (ranks.get(b.stage_code) ?? stages.length)
                    || a.id - b.id
                ),
            }));
    }

    get visibleSelectableRows() {
        return this.filteredRows.filter((row) => row.can_run);
    }

    get allVisibleSelected() {
        return this.visibleSelectableRows.length > 0
            && this.visibleSelectableRows.every((row) => this.state.selectedIds[row.id]);
    }

    get selectedCount() {
        return this.filteredRows.filter(
            (row) => row.can_run && this.state.selectedIds[row.id]
        ).length;
    }

    get hasDirtyRows() {
        return Object.values(this.state.dirtyRows).some(Boolean);
    }

    get activeStageLabel() {
        return this.state.stages.find((stage) => stage.code === this.state.activeStage)?.label
            || "المرحلة الحالية";
    }

    get isFinalStage() {
        return this.state.activeStage === "final";
    }

    get ruleModel() {
        return this.isFinalStage ? FINAL_RULE_MODEL : RULE_MODEL;
    }

    get rpcPrefix() {
        return this.isFinalStage ? "final_replenishment" : "stage_replenishment";
    }

    get isBusy() {
        return this.state.isLoading || this.state.isSyncing || this.state.isRunningAll
            || Object.values(this.state.savingRows || {}).some(Boolean)
            || Object.values(this.state.runningRows || {}).some(Boolean);
    }

    get stageRunRows() {
        if (this.state.activeStage === "all") {
            return this.state.rows.filter((row) => row.can_run);
        }
        return this.state.rows.filter(
            (row) => row.stage_code === this.state.activeStage && row.can_run
        );
    }

    formatQty(value) {
        const quantity = Number(value || 0);
        return this.quantityFormatter.format(Number.isFinite(quantity) ? quantity : 0);
    }

    statusLabel(row) {
        return STATUS_LABELS[row.status] || row.status_label || "—";
    }

    statusIcon(row) {
        return STATUS_ICONS[row.status] || "fa-circle-o";
    }

    statusClass(row) {
        return `o_fsr_status o_fsr_status_${row.status || "disabled"}`;
    }

    qtyClass(value, highlight = false) {
        const quantity = Number(value || 0);
        if (highlight && quantity > 0) {
            return "o_fsr_qty o_fsr_qty_need";
        }
        if (quantity > 0) {
            return "o_fsr_qty o_fsr_qty_positive";
        }
        return "o_fsr_qty o_fsr_qty_zero";
    }

    stageTabClass(code) {
        return `o_fsr_stage_tab ${this.state.activeStage === code ? "is-active" : ""}`;
    }

    errorMessage(error, fallback) {
        return error?.data?.message || error?.message || fallback;
    }

    guardUnsavedChanges() {
        if (!this.hasDirtyRows) {
            return true;
        }
        this.notification.add(
            "احفظ تعديلات Min و Max الظاهرة أولًا حتى لا تضيع القيم المكتوبة.",
            { type: "warning" }
        );
        return false;
    }

    applyData(data, clearSelection = true) {
        if (this.isFinalStage) {
            this.finalData = data;
        } else {
            this.stageData = data;
        }
        const rows = (data?.rows || []).map((row) => this.isFinalStage ? {
            ...row, stage_code: "final", stage: "المنتج التام",
            current_qty: row.finished_qty,
        } : { ...row }).sort(
            (left, right) => stageDisplayRank(left.stage_code) - stageDisplayRank(right.stage_code)
        );
        const stages = [...(this.stageData?.stages || []), {
            code: "final", label: "المنتج التام",
            total: this.finalData?.summary?.total_rules || 0,
            need: this.finalData?.summary?.need_production || 0,
        }].sort(
            (left, right) => stageDisplayRank(left.code) - stageDisplayRank(right.code)
        );
        this.state.rows = rows;
        this.state.stages = stages;
        this.state.summary = data?.summary || this.state.summary;
        this.state.dirtyRows = {};

        if (
            !this.state.stages.some((stage) => stage.code === this.state.activeStage)
        ) {
            this.state.activeStage = stages[0]?.code || "carpentry";
        }

        if (clearSelection) {
            this.state.selectedIds = {};
            return;
        }
        const rowIds = new Set(rows.map((row) => row.id));
        this.state.selectedIds = Object.fromEntries(
            Object.entries(this.state.selectedIds).filter(
                ([id, selected]) => selected && rowIds.has(Number(id))
            )
        );
    }

    async loadData(force = false) {
        force = force === true;
        if (!force && !this.guardUnsavedChanges()) {
            return;
        }
        this.state.isLoading = true;
        try {
            // Sequential synchronization preserves the existing stage/final
            // coordinator order. Neither endpoint generates production here.
            const stageData = await this.orm.call(
                RULE_MODEL,
                "stage_replenishment_dashboard_data",
                []
            );
            const finalData = await this.orm.call(
                FINAL_RULE_MODEL, "final_replenishment_dashboard_data", []
            );
            this.stageData = stageData;
            this.finalData = finalData;
            this.applyData(this.isFinalStage ? finalData : stageData);
        } catch (error) {
            console.error("Unable to load stage replenishment dashboard", error);
            this.notification.add(
                this.errorMessage(error, "تعذر تحميل بيانات Min/Max للمراحل."),
                { type: "danger" }
            );
        } finally {
            this.state.isLoading = false;
        }
    }

    async syncRules() {
        if (!this.guardUnsavedChanges()) {
            return;
        }
        this.state.isSyncing = true;
        try {
            const data = await this.orm.call(
                this.ruleModel,
                `${this.rpcPrefix}_sync_rules`,
                []
            );
            this.applyData(data);
            this.notification.add("تم تحديث الأصناف والموديلات من الريسيبي بنجاح.", {
                type: "success",
            });
        } catch (error) {
            console.error("Unable to sync stage replenishment rules", error);
            this.notification.add(
                this.errorMessage(error, "تعذر تحديث الأصناف من الريسيبي."),
                { type: "danger" }
            );
        } finally {
            this.state.isSyncing = false;
        }
    }

    setStage(code) {
        if (this.isBusy || !this.guardUnsavedChanges()
            || !this.state.stages.some((stage) => stage.code === code)) {
            return;
        }
        const changesStore = (code === "final") !== this.isFinalStage;
        this.state.activeStage = code;
        this.state.selectedIds = {};
        if (changesStore) {
            this.applyData(this.isFinalStage ? this.finalData : this.stageData);
        }
    }

    setStatus(event) {
        this.state.status = event.target.value;
        this.state.selectedIds = {};
    }

    updateSearch(event) {
        this.state.search = event.target.value;
        this.state.selectedIds = {};
    }

    toggleRow(row) {
        if (!row.can_run) {
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
        this.state.dirtyRows[row.id] = true;
    }

    async saveLimits(row) {
        const minimum = Number(row.minimum);
        const maximum = Number(row.maximum);
        if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) {
            this.notification.add("اكتب رقمًا صحيحًا في الحد الأدنى والحد الأقصى.", {
                type: "warning",
            });
            return;
        }
        if (minimum < 0 || maximum < 0) {
            this.notification.add("الحد الأدنى والحد الأقصى لا يمكن أن يكونا بالسالب.", {
                type: "warning",
            });
            return;
        }
        if (minimum > maximum) {
            this.notification.add("الحد الأدنى يجب أن يكون أقل من أو يساوي الحد الأقصى.", {
                type: "warning",
            });
            return;
        }

        this.state.savingRows[row.id] = true;
        const pendingRows = this.state.rows
            .filter((candidate) => (
                candidate.id !== row.id && this.state.dirtyRows[candidate.id]
            ))
            .map((candidate) => ({
                id: candidate.id,
                minimum: candidate.minimum,
                maximum: candidate.maximum,
            }));
        try {
            const data = await this.orm.call(
                this.ruleModel,
                `${this.rpcPrefix}_update_limits`,
                [row.id, minimum, maximum]
            );
            this.applyData(data, false);
            for (const pending of pendingRows) {
                const pendingRow = this.state.rows.find(
                    (candidate) => candidate.id === pending.id
                );
                if (pendingRow) {
                    pendingRow.minimum = pending.minimum;
                    pendingRow.maximum = pending.maximum;
                    this.state.dirtyRows[pending.id] = true;
                }
            }
            this.notification.add(`تم حفظ حدود ${row.product} — ${row.model}.`, {
                type: "success",
            });
        } catch (error) {
            console.error("Unable to save stage replenishment limits", error);
            this.notification.add(
                this.errorMessage(error, "تعذر حفظ الحد الأدنى والحد الأقصى."),
                { type: "danger" }
            );
        } finally {
            this.state.savingRows[row.id] = false;
        }
    }

    async runRow(row) {
        await this.runIds([row.id]);
    }

    async runSelected() {
        const ids = this.filteredRows
            .filter((row) => row.can_run && this.state.selectedIds[row.id])
            .map((row) => row.id);
        if (!ids.length) {
            this.notification.add("اختار صنفًا واحدًا على الأقل للتصنيع.", {
                type: "warning",
            });
            return;
        }
        await this.runIds(ids);
    }

    async runAll() {
        const ruleIds = this.stageRunRows.map((row) => row.id);
        if (Array.isArray(ruleIds) && !ruleIds.length) {
            this.notification.add(`لا يوجد احتياج تصنيع في ${this.activeStageLabel}.`, {
                type: "info",
            });
            return;
        }
        this.state.isRunningAll = true;
        try {
            await this.runIds(ruleIds, true);
        } finally {
            this.state.isRunningAll = false;
        }
    }

    async runIds(ruleIds, isBulk = false) {
        if (!this.guardUnsavedChanges()) {
            return;
        }
        const ids = Array.isArray(ruleIds) ? ruleIds : [];
        for (const id of ids) {
            this.state.runningRows[id] = true;
        }
        try {
            const result = await this.orm.call(
                this.ruleModel,
                `${this.rpcPrefix}_run_now`,
                [ruleIds]
            );
            if (result?.data) {
                this.applyData(result.data);
            }
            const count = Number(result?.created_count || 0);
            if (count) {
                this.notification.add(
                    `تم تجهيز ${count} أمر تصنيع كمسودة في انتظار التأكيد.`,
                    { type: "success" }
                );
            } else {
                this.notification.add("لا يوجد عجز جديد يحتاج أمر تصنيع.", {
                    type: "info",
                });
            }
            if (result?.action) {
                await this.action.doAction(result.action);
            } else if (!result?.data && !isBulk) {
                await this.loadData(true);
            }
        } catch (error) {
            console.error("Unable to run stage replenishment", error);
            this.notification.add(
                this.errorMessage(error, "تعذر إنشاء أوامر التصنيع التعويضية."),
                { type: "danger" }
            );
        } finally {
            for (const id of ids) {
                this.state.runningRows[id] = false;
            }
        }
    }

    async openOrders(row = null) {
        if (!this.guardUnsavedChanges()) {
            return;
        }
        try {
            const result = await this.orm.call(
                this.ruleModel,
                `${this.rpcPrefix}_open_orders`,
                [row ? row.id : false]
            );
            if (result) {
                await this.action.doAction(result);
            }
        } catch (error) {
            console.error("Unable to open stage replenishment orders", error);
            this.notification.add(
                this.errorMessage(error, "تعذر فتح أوامر التصنيع التعويضية."),
                { type: "danger" }
            );
        }
    }

    openLastProduction(row) {
        if (!row.last_production_id || !this.guardUnsavedChanges()) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: row.last_production || "أمر التصنيع",
            res_model: "furniture.mrp.production",
            res_id: row.last_production_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openListView() {
        if (this.isFinalStage || !this.guardUnsavedChanges()) {
            return;
        }
        this.action.doAction(
            "furniture_stage_replenishment.action_furniture_stage_replenishment_rule_list"
        );
    }
}

StageReplenishmentDashboard.template =
    "furniture_stage_replenishment.StageReplenishmentDashboard";

registry.category("actions").add(
    "furniture_stage_replenishment.stage_replenishment_dashboard",
    StageReplenishmentDashboard
);
