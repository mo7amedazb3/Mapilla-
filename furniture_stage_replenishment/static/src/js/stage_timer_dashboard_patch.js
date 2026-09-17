/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { FurnitureMrpStageDashboardPage } from "@furniture_mrp/js/mrp_stage_dashboard";

function parseServerDate(value) {
    if (!value) {
        return false;
    }
    const text = String(value);
    const normalized = text.includes("T") ? text : `${text.replace(" ", "T")}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? false : date;
}

function timerRemainingSeconds(timer) {
    if (!timer) {
        return 0;
    }
    let remaining = Number(timer.remaining_seconds || 0);
    if (!timer.running || timer.paused) {
        return Math.round(remaining);
    }
    const snapshot = parseServerDate(timer.snapshot_at);
    if (!snapshot) {
        return Math.round(remaining);
    }
    const now = Date.now();
    if (timer.continuous) {
        remaining -= Math.max(now - snapshot.getTime(), 0) / 1000;
        return Math.round(remaining);
    }
    for (const interval of timer.work_intervals || []) {
        const start = parseServerDate(interval?.[0]);
        const finish = parseServerDate(interval?.[1]);
        if (!start || !finish) {
            continue;
        }
        const overlapStart = Math.max(snapshot.getTime(), start.getTime());
        const overlapFinish = Math.min(now, finish.getTime());
        if (overlapFinish > overlapStart) {
            remaining -= (overlapFinish - overlapStart) / 1000;
        }
    }
    return Math.round(remaining);
}

function formatSignedDuration(seconds) {
    const rounded = Math.round(seconds || 0);
    const sign = rounded < 0 ? "-" : "";
    let value = Math.abs(rounded);
    const hours = Math.floor(value / 3600);
    value -= hours * 3600;
    const minutes = Math.floor(value / 60);
    const secs = value % 60;
    return `${sign}${[hours, minutes, secs]
        .map((part) => String(part).padStart(2, "0"))
        .join(":")}`;
}

patch(FurnitureMrpStageDashboardPage.prototype, {
    stageTimerValue(timer) {
        void this.stageDashboard.clockTick;
        return formatSignedDuration(timerRemainingSeconds(timer));
    },

    stageTimerClass(timer) {
        const remaining = timerRemainingSeconds(timer);
        if (timer?.paused) {
            return "o_furniture_stage_timer is-paused";
        }
        if (remaining < 0) {
            return "o_furniture_stage_timer is-overdue";
        }
        if (timer?.running) {
            return "o_furniture_stage_timer is-running";
        }
        return "o_furniture_stage_timer is-finished";
    },

    stageTimerCaption(timer) {
        if (timer?.paused) {
            return _t("متوقف مؤقتًا");
        }
        if (timerRemainingSeconds(timer) < 0) {
            return _t("وقت زائد");
        }
        if (timer?.finished_at) {
            return _t("المتبقي عند الإنهاء");
        }
        return _t("الوقت المتبقي");
    },

    async pauseProductBatchTimer(batch) {
        if (!batch?.timer?.can_pause) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () =>
            this._callProductBatchAction(
                "action_stage_dashboard_pause_product_batch_timer",
                batch
            )
        );
    },

    async resumeProductBatchTimer(batch) {
        if (!batch?.timer?.can_resume) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () =>
            this._callProductBatchAction(
                "action_stage_dashboard_resume_product_batch_timer",
                batch
            )
        );
    },

    async pauseOrderTimer(order) {
        if (!order?.timer?.can_pause) {
            return;
        }
        await this._runOrderSupervisorAction(order, "pause-timer", async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_pause_order_timer",
                [[Number(order.id)]],
                { stage_code: this.selectedStageCode, context: this.context }
            )
        );
    },

    async resumeOrderTimer(order) {
        if (!order?.timer?.can_resume) {
            return;
        }
        await this._runOrderSupervisorAction(order, "resume-timer", async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_resume_order_timer",
                [[Number(order.id)]],
                { stage_code: this.selectedStageCode, context: this.context }
            )
        );
    },
});
