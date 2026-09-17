/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useService } from "@web/core/utils/hooks";

import { ArabicAttendanceDatePicker, ManualAttendanceDateTime } from "@factory_attendance_dashboard/js/attendance_dashboard";

export class OvertimeApproval extends Component {
    static components = { ArabicAttendanceDatePicker, ManualAttendanceDateTime };
    static template = "factory_attendance_overtime.OvertimeApproval";

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            loading: true, savingId: false, error: "", selectedDate: "",
            selectedStatus: "all", query: "", selectedKeys: [],
            data: { rows: [], counts: { all: 0, pending: 0, approved: 0, rejected: 0, open: 0 }, date: "", date_label: "", today: "", server_time: "--:--" },
            manual: { open: false, row: null, checkout: "", error: "" },
        });
        this.requestToken = 0;
        onWillStart(() => this.load());
    }

    get filteredRows() {
        const query = this.state.query.trim().toLocaleLowerCase();
        return (this.state.selectedStatus === "lunch" ? (this.state.data.lunch_rows || []) : this.state.data.rows).filter((row) =>
            (["all", "lunch"].includes(this.state.selectedStatus) || row.state === this.state.selectedStatus) &&
            (!query || `${row.employee_name} ${row.department}`.toLocaleLowerCase().includes(query))
        );
    }

    rowKey(row) {
        return `${row.kind || "normal"}:${row.attendance_id}`;
    }

    get selectableRows() {
        return this.filteredRows.filter((row) => !row.is_open && row.state === "pending");
    }

    get selectedRows() {
        const selected = new Set(this.state.selectedKeys);
        const rows = [...this.state.data.rows, ...(this.state.data.lunch_rows || [])];
        return rows.filter((row) => !row.is_open && row.state === "pending" && selected.has(this.rowKey(row)));
    }

    get allFilteredSelected() {
        return Boolean(this.selectableRows.length) && this.selectableRows.every((row) => this.isSelected(row));
    }

    isSelected(row) {
        return this.state.selectedKeys.includes(this.rowKey(row));
    }

    toggleRow(row, checked) {
        if (this.state.savingId || row.is_open || row.state !== "pending") return;
        const key = this.rowKey(row);
        const selected = new Set(this.state.selectedKeys);
        if (checked) selected.add(key);
        else selected.delete(key);
        this.state.selectedKeys = [...selected];
    }

    toggleAllFiltered(checked) {
        if (this.state.savingId) return;
        const selected = new Set(this.state.selectedKeys);
        for (const row of this.selectableRows) {
            const key = this.rowKey(row);
            if (checked) selected.add(key);
            else selected.delete(key);
        }
        this.state.selectedKeys = [...selected];
    }

    async load() {
        if (this.state.savingId) return;
        const token = ++this.requestToken;
        this.state.loading = true;
        this.state.error = "";
        this.state.selectedKeys = [];
        try {
            const data = await this.orm.call("hr.employee", "factory_overtime_approval_data", [this.state.selectedDate || false]);
            if (token !== this.requestToken) return;
            this.state.data = data;
            this.state.selectedDate = data.date;
        } catch (error) {
            if (token === this.requestToken) this.state.error = error.data?.message || error.message || "تعذر تحميل اعتماد الإضافي.";
        } finally {
            if (token === this.requestToken) this.state.loading = false;
        }
    }

    async onCalendarDateChanged(value) {
        if (this.state.savingId) return;
        this.state.selectedDate = value;
        await this.load();
    }

    selectStatus(status) {
        this.state.selectedStatus = status;
        this.state.selectedKeys = [];
    }
    statusLabel(state) { return { pending: "بحاجة للاعتماد", approved: "معتمد", rejected: "مرفوض" }[state] || state; }
    sourceIcon(source) {
        return { auto: "fa fa-moon-o", late_biometric: "fa fa-hand-pointer-o", biometric: "fa fa-hand-pointer-o", manual: "fa fa-pencil" }[source] || "fa fa-clock-o";
    }

    confirmDecision(row, decision) {
        if (this.state.savingId || row.is_open || row.state !== "pending") return;
        const approve = decision === "approve";
        this.dialog.add(ConfirmationDialog, {
            title: approve ? "اعتماد الوقت الإضافي" : "رفض الوقت الإضافي",
            body: row.kind === "lunch" ? `${approve ? "اعتماد" : "رفض"} ساعة إضافي الغداء لـ ${row.employee_name}؟` : approve
                ? `اعتماد انصراف ${row.employee_name} الساعة ${row.detected_checkout} واحتساب ${row.overtime_label} إضافي؟`
                : `رفض إضافي ${row.employee_name}؟ سيبقى وقت الانصراف محفوظًا ولن تُحسب له دقائق إضافية.`,
            confirmLabel: approve ? "اعتماد" : "رفض الإضافي", cancelLabel: "رجوع",
            confirmClass: approve ? "btn-primary" : "btn-danger",
            confirm: () => this.decide(row, decision),
        });
    }

    confirmChangeDecision(row) {
        if (this.state.savingId || !["approved", "rejected"].includes(row.state)) return;
        const decision = row.state === "approved" ? "reject" : "approve";
        const approving = decision === "approve";
        const kind = row.kind === "lunch" ? "إضافي الغداء" : "الوقت الإضافي";
        this.dialog.add(ConfirmationDialog, {
            title: "تغيير قرار الإضافي",
            body: approving
                ? `تغيير قرار ${kind} لـ ${row.employee_name} من مرفوض إلى معتمد؟`
                : `تغيير قرار ${kind} لـ ${row.employee_name} من معتمد إلى مرفوض؟`,
            confirmLabel: approving ? "تغيير إلى معتمد" : "تغيير إلى مرفوض",
            cancelLabel: "إلغاء",
            confirmClass: approving ? "btn-primary" : "btn-danger",
            confirm: () => this.decide(row, decision, false, true),
        });
    }

    confirmBulkDecision(decision) {
        if (this.state.savingId || !this.selectedRows.length) return;
        const approve = decision === "approve";
        const count = this.selectedRows.length;
        const kind = this.state.selectedStatus === "lunch" ? "إضافي الغداء" : "الإضافي العادي";
        this.dialog.add(ConfirmationDialog, {
            title: approve ? "اعتماد المحددين" : "رفض المحددين",
            body: `${approve ? "اعتماد" : "رفض"} ${kind} لـ ${count} عامل؟ سيتم تطبيق القرار على كل العمال المحددين.`,
            confirmLabel: approve ? "اعتماد المحددين" : "رفض المحددين",
            cancelLabel: "رجوع",
            confirmClass: approve ? "btn-primary" : "btn-danger",
            confirm: () => this.bulkDecide(decision),
        });
    }

    async bulkDecide(decision) {
        const selectedRows = this.selectedRows;
        if (this.state.savingId || !selectedRows.length) return;
        this.state.savingId = "bulk";
        this.state.error = "";
        try {
            const items = selectedRows.map((row) => ({
                attendance_id: row.attendance_id,
                kind: row.kind || "normal",
                expected_version: row.expected_version,
            }));
            const result = await this.orm.call(
                "hr.employee", "factory_overtime_approval_bulk_decide", [items, decision]
            );
            this.notification.add(
                result.state === "approved"
                    ? `تم اعتماد الإضافي لـ ${result.count} عامل`
                    : `تم رفض الإضافي لـ ${result.count} عامل`,
                { type: result.state === "approved" ? "success" : "warning" }
            );
            this.state.savingId = false;
            await this.load();
        } catch (error) {
            this.state.error = error.data?.message || error.message || "تعذر حفظ القرار الجماعي.";
        } finally {
            this.state.savingId = false;
        }
    }

    openManual(row) {
        if (this.state.savingId || row.is_open || row.state !== "pending" || row.kind === "lunch") return;
        Object.assign(this.state.manual, { open: true, row, checkout: row.custom_checkout, date: row.custom_checkout.split("T")[0], error: "" });
    }

    onManualDate(value) {
        if (this.state.savingId) return;
        this.state.manual.date = value;
        const clock = this.state.manual.checkout.split('T')[1];
        this.state.manual.checkout = clock ? `${value}T${clock}` : '';
    }

    onManualTime(value) {
        if (!this.state.savingId) this.state.manual.checkout = value;
    }

    closeManual() {
        if (this.state.savingId) return;
        Object.assign(this.state.manual, { open: false, row: null, checkout: "", error: "" });
    }

    async decide(row, decision, checkout = false, allowFinalChange = false) {
        if (this.state.savingId || (!allowFinalChange && row.state !== "pending")) return;
        this.state.savingId = row.attendance_id;
        this.state.error = "";
        this.state.manual.error = "";
        try {
            const result = await this.orm.call(
                "hr.employee", row.kind === "lunch" ? "factory_lunch_overtime_decide" : "factory_overtime_approval_decide",
                [row.attendance_id, decision, row.expected_version],
                row.kind === "lunch" ? {} : { checkout_local: checkout || false }
            );
            this.notification.add(
                result.state === "approved" ? `تم اعتماد إضافي ${result.employee_name}: ${result.approved_label}` : `تم رفض إضافي ${result.employee_name}`,
                { type: result.state === "approved" ? "success" : "warning" }
            );
            Object.assign(this.state.manual, { open: false, row: null, checkout: "", error: "" });
            this.state.savingId = false;
            await this.load();
        } catch (error) {
            const message = error.data?.message || error.message || "تعذر حفظ القرار.";
            if (this.state.manual.open) this.state.manual.error = message;
            else this.state.error = message;
        } finally {
            this.state.savingId = false;
        }
    }

    approveManual() {
        const row = this.state.manual.row;
        if (!row || !this.state.manual.checkout) {
            this.state.manual.error = "حدد تاريخ ووقت الانصراف.";
            return;
        }
        return this.decide(row, "manual", this.state.manual.checkout);
    }
}

registry.category("actions").add("factory_attendance_overtime.approval", OvertimeApproval);
