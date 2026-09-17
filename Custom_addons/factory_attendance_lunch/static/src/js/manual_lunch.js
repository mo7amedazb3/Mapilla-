/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { FactoryAttendanceDashboard } from "@factory_attendance_dashboard/js/attendance_dashboard";
patch(FactoryAttendanceDashboard.prototype, {
    async openManualAttendanceDialog(employee) {
        if (this.state.manualDialog.processing) return;
        this.state.manualDialog.lunchEnabled = employee.lunch_enabled !== false;
        this.state.manualDialog.lunchMode = this.state.selectedStatus === "lunch" && this.state.manualDialog.lunchEnabled;
        return super.openManualAttendanceDialog(employee);
    },
    async selectManualAttendanceMode(lunchMode) {
        const d = this.state.manualDialog;
        if (d.processing || d.loading || d.lunchMode === lunchMode || (lunchMode && !d.lunchEnabled)) return;
        d.lunchMode = lunchMode;
        d.records = []; d.attendanceId = false; d.checkIn = ""; d.checkOut = "";
        d.error = "";
        await this.loadManualAttendanceDetails();
    },
    async loadManualAttendanceDetails() {
        const d = this.state.manualDialog;
        if (!d.lunchMode) return super.loadManualAttendanceDetails();
        const token = ++this.manualDialogToken;
        d.loading = true; d.error = "";
        try {
            const data = await this.orm.call("hr.employee", "factory_manual_lunch_details", [d.employeeId, d.selectedDate]);
            if (token !== this.manualDialogToken || !d.open) return;
            Object.assign(d, {employeeName:data.employee_name, selectedDate:data.selected_date, today:data.today,
                timezone:data.timezone, records:data.records, attendanceId:data.default_attendance_id});
            this.applyManualAttendanceRecord(d.attendanceId);
        } catch (e) {
            if (token === this.manualDialogToken) d.error = e.data?.message || e.message;
        } finally { if (token === this.manualDialogToken) d.loading = false; }
    },
    async saveManualAttendance() {
        const d = this.state.manualDialog;
        if (!d.lunchMode) return super.saveManualAttendance();
        if (d.processing || d.loading) return;
        const record = d.records.find(r => r.id === d.attendanceId);
        if (!record || !d.checkIn) { d.error = "اختر استراحة وحدد وقت الخروج للغداء."; return; }
        d.processing = true; d.error = "";
        try {
            await this.orm.call("hr.employee", "factory_save_manual_lunch", [d.employeeId,d.selectedDate,d.attendanceId,d.checkIn,d.checkOut || false,record.version]);
            this.notification.add("تم تعديل مواعيد الغداء.", {type:"success"});
            d.processing = false; this.closeManualDialog(); await this.loadDashboard();
        } catch(e) { d.error = e.data?.message || e.message; }
        finally { d.processing = false; }
    },
});
