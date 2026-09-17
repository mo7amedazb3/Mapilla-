/** @odoo-module **/
import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export class ApprovedTracking extends Component {
    static template = "furniture_need_to_produce.ApprovedTracking";
    static props = ["*"];
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.workspace = useRef("workspace");
        this.state = useState({scope: "final", search: "", status: "all", cards: [], total: 0,
            page: 0, pages: 1, loading: true, busy: false, error: "", models: [], counts: {},
            modelId: null, modelName: ""});
        this.labels = {waiting: "بانتظار التنفيذ", running: "جاري التنفيذ", done: "مكتمل", attention: "بحاجة إلى المراجعة"};
        this.request = 0;
        onWillStart(() => this.load());
        onWillUnmount(() => { this.request++; });
    }
    async load(showSpinner = true) {
        const request = ++this.request;
        if (showSpinner) this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("furniture.need.to.produce", "get_approved_tracking", [], {
                scope: this.state.scope, search: this.state.search, status: this.state.status, page: this.state.page,
                model_id: this.state.modelId,
            });
            if (request === this.request) Object.assign(this.state, data);
        } catch (error) {
            if (request === this.request) this.state.error = error.data?.message || "تعذر تحميل المتابعة. أعد المحاولة.";
        } finally {
            if (request === this.request) this.state.loading = false;
        }
    }
    selectScope(scope) {
        if (this.state.busy || scope === this.state.scope) return;
        Object.assign(this.state, {scope, page: 0, cards: [], total: 0, models: [], counts: {}, modelId: null, modelName: ""});
        return this.load();
    }
    async openModel(model) {
        if (this.state.busy) return;
        Object.assign(this.state, {modelId: model.id, modelName: model.name, page: 0});
        await this.load();
        if (this.workspace.el) this.workspace.el.scrollTop = 0;
    }
    async backToModels() {
        if (this.state.busy) return;
        Object.assign(this.state, {modelId: null, modelName: "", page: 0});
        await this.load();
        if (this.workspace.el) this.workspace.el.scrollTop = 0;
    }
    applyFilter() { this.state.page = 0; return this.load(); }
    changeStatus(event) { this.state.status = event.target.value; return this.applyFilter(); }
    changePage(delta) { this.state.page += delta; return this.load(); }
    get modelGroups() {
        const groups = new Map();
        for (const card of this.state.cards) {
            const name = card.model || "بدون موديل";
            if (!groups.has(card.model_id)) groups.set(card.model_id, {id: card.model_id, name, cards: [], quantity: 0});
            const group = groups.get(card.model_id);
            group.cards.push(card);
            group.quantity += card.quantity;
        }
        return [...groups.values()];
    }
    get summary() {
        return Object.entries(this.labels).map(([key, label]) => ({
            key, label, count: this.state.counts[key] || 0,
            icon: {waiting: "clock-o", running: "cogs", done: "check-circle", attention: "exclamation-circle"}[key],
        }));
    }
    stageIcon(step) {
        if (step.material_shortage) return "exclamation-triangle";
        if (step.state === "done") return "check";
        if (step.state === "cancelled") return "times";
        const icons = {"التقديم": "paint-brush", "تجميع": "cubes", "القواعد": "th-large",
            "تجهيز": "wrench", "تفصيل": "scissors", "كسوه": "diamond", "كسوة": "diamond",
            "تصنيع دهانات": "tint", "التغليف": "archive"};
        return icons[step.label] || "cog";
    }
    timeline(card) {
        const grouped = new Map();
        for (const step of card.steps) {
            const key = step.code || step.key.split(":").pop();
            if (!grouped.has(key)) grouped.set(key, {...step, key, children: [], production_ids: []});
            const item = grouped.get(key);
            item.children.push(step);
            if (!item.production_ids.includes(step.production_id)) item.production_ids.push(step.production_id);
        }
        return [...grouped.values()].map(item => {
            const states = item.children.map(step => step.state);
            item.state = states.includes("cancelled") ? "cancelled" : states.every(state => state === states[0]) ? states[0] : "in_progress";
            item.material_shortage = item.children.some(step => step.material_shortage);
            item.material_shortage_summary = item.children.filter(step => step.material_shortage)
                .map(step => step.material_shortage_summary).filter(Boolean).join("، ");
            item.state_label = {pending: "في الانتظار", done: "مكتمل", in_progress: "جاري التنفيذ", quality_check: "فحص الجودة", cancelled: "ملغي"}[item.state];
            item.production_name = item.production_ids.length === 1 ? item.children[0].production_name : `${item.production_ids.length} أوامر إنتاج`;
            return item;
        });
    }
    openStage(step) {
        if (step.production_ids.length === 1) return this.openOrder(step.production_ids[0]);
        return this.action.doAction({type: "ir.actions.act_window", name: step.label,
            res_model: "furniture.mrp.production", domain: [["id", "in", step.production_ids]],
            views: [[false, "list"], [false, "form"]], target: "current"});
    }
    currentStage(card) {
        if (card.status === "attention") return "خامات غير متوفرة: " + card.shortage_stage_names;
        const active = this.timeline(card).filter(step => ["in_progress", "quality_check"].includes(step.state));
        if (active.length) return active.map(step => step.label).join("، ");
        if (card.status === "done") return "اكتملت مراحل الإنتاج";
        const next = card.steps.find(step => step.state === "pending");
        return next ? "بانتظار بدء " + next.label : this.labels[card.status];
    }
    displayDate(value) {
        if (!value) return "—";
        return new Intl.DateTimeFormat("ar-EG", {dateStyle: "medium", timeStyle: "short"})
            .format(new Date(value.replace(" ", "T") + "Z"));
    }
    async openOrders(card) {
        const action = await this.orm.call("furniture.need.to.produce", "action_open_productions", [card.piece_ids]);
        return this.action.doAction(action);
    }
    openOrder(id) {
        return this.action.doAction({type: "ir.actions.act_window", res_model: "furniture.mrp.production",
            res_id: id, views: [[false, "form"]], target: "current"});
    }
    cancel(card) {
        if (this.state.busy || !card.can_cancel) return;
        this.state.busy = true;
        this.dialog.add(ConfirmationDialog, {
            title: "إلغاء الاعتماد وحذف أوامر الإنتاج",
            body: `سيتم إلغاء وحذف ${card.order_count} أمر إنتاج مرتبط بهذا الكارت، وإعادة ${card.quantity} قطعة إلى بانتظار الاعتماد. ${card.quantity > 1 ? "يشمل الإلغاء جميع الاعتمادات والقطع المجمّعة في هذا الكارت. " : ""}لن يتم حذف أوامر مصادر المخزون أو أي أوامر خارج هذا الكارت. هل تريد المتابعة؟`,
            confirmLabel: "إلغاء الاعتماد وحذف الأوامر",
            confirmClass: "btn-danger",
            cancelLabel: "رجوع",
            confirm: async () => {
                try {
                    await this.orm.call("furniture.need.to.produce", "action_cancel_approval", [card.piece_ids], {approval_token: card.approval_token});
                    this.notification.add("تم إلغاء الاعتماد وحذف أوامره وإعادة القطع للمراجعة.", {type: "success"});
                } catch (error) {
                    this.notification.add(error.data?.message || "تعذر الإلغاء؛ لم تُحفظ تغييرات العملية.", {type: "danger", sticky: true});
                } finally {
                    this.state.busy = false;
                    await this.load();
                }
            },
            cancel: () => { this.state.busy = false; },
        }, {onClose: () => { this.state.busy = false; }});
    }
}
registry.category("actions").add("furniture_approved_tracking", ApprovedTracking);
