/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillDestroy,
    onWillStart,
    useState,
    useEffect,
    useRef,
    useExternalListener,
    onWillUpdateProps,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const REFRESH_INTERVAL = 60 * 1000;
const ARABIC_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];
const ARABIC_WEEK_DAYS = ["السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"];

export class ArabicAttendanceDatePicker extends Component {
    static template = "factory_attendance_dashboard.ArabicAttendanceDatePicker";
    static props = { value: String, today: String, serverTime: String, onChange: Function, compact: { type: Boolean, optional: true }, disabled: { type: Boolean, optional: true }, caption: { type: String, optional: true } };
    setup() {
        this.weekDays = ARABIC_WEEK_DAYS;
        this.root = useRef("dateCard");
        this.state = useState({ calendar: { open: false, viewYear: 0, viewMonth: 0 } });
        useExternalListener(document, "click", (event) => {
            if (this.state.calendar.open && !this.root.el?.contains(event.target)) this.closeDatePicker();
        });
        useExternalListener(document, "keydown", (event) => {
            if (event.key === "Escape" && this.state.calendar.open) {
                this.closeDatePicker();
                this.root.el?.focus({ preventScroll: true });
            }
        });
    }
    _parseIsoDate(value) {
        const parts = String(value || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
            return null;
        }
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    openDatePicker(event) {
        if (this.props.disabled) return;
        event?.preventDefault();
        event?.stopPropagation();
        if (this.state.calendar.open) {
            return;
        }
        const selected = this._parseIsoDate(
            this.props.value || this.props.today
        ) || new Date();
        this.state.calendar.viewYear = selected.getFullYear();
        this.state.calendar.viewMonth = selected.getMonth();
        this.state.calendar.open = true;
    }

    closeDatePicker() {
        this.state.calendar.open = false;
    }

    stopCalendarPropagation(event) {
        event.stopPropagation();
    }

    onDateCardKeydown(event) {
        if (event.target !== event.currentTarget) {
            return;
        }
        if (event.key === "Enter" || event.key === " ") {
            this.openDatePicker(event);
        }
    }

    _isoDate(date) {
        const pad = (value) => String(value).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    arabicNumber(value) {
        return new Intl.NumberFormat("ar-EG", { useGrouping: false }).format(value);
    }

    get selectedDateLongLabel() {
        const selected = this._parseIsoDate(this.props.value);
        if (!selected) {
            return "اختر اليوم";
        }
        return new Intl.DateTimeFormat("ar-EG", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
        }).format(selected);
    }

    get calendarMonthLabel() {
        const calendar = this.state.calendar;
        return `${ARABIC_MONTHS[calendar.viewMonth] || ""} ${this.arabicNumber(calendar.viewYear)}`;
    }

    get calendarDays() {
        const { viewYear, viewMonth } = this.state.calendar;
        if (!viewYear) {
            return [];
        }
        const firstDay = new Date(viewYear, viewMonth, 1);
        // السبت هو أول عمود في التقويم العربي.
        const leadingDays = (firstDay.getDay() + 1) % 7;
        const gridStart = new Date(viewYear, viewMonth, 1 - leadingDays);
        const today = this.props.today || "";
        return Array.from({ length: 42 }, (_, index) => {
            const date = new Date(
                gridStart.getFullYear(),
                gridStart.getMonth(),
                gridStart.getDate() + index
            );
            const iso = this._isoDate(date);
            return {
                key: iso,
                iso,
                label: this.arabicNumber(date.getDate()),
                currentMonth: date.getMonth() === viewMonth,
                selected: iso === this.props.value,
                today: iso === today,
                future: Boolean(today && iso > today),
            };
        });
    }

    calendarDayClass(day) {
        return [
            "o_factory_calendar_day",
            day.currentMonth ? "is-current-month" : "is-other-month",
            day.selected ? "is-selected" : "",
            day.today ? "is-today" : "",
            day.future ? "is-future" : "",
        ].filter(Boolean).join(" ");
    }

    changeCalendarMonth(offset, event) {
        event?.preventDefault();
        event?.stopPropagation();
        const calendar = this.state.calendar;
        const target = new Date(calendar.viewYear, calendar.viewMonth + offset, 1);
        if (offset > 0 && this.nextCalendarMonthDisabled) {
            return;
        }
        calendar.viewYear = target.getFullYear();
        calendar.viewMonth = target.getMonth();
    }

    get nextCalendarMonthDisabled() {
        const today = this._parseIsoDate(this.props.today);
        if (!today || !this.state.calendar.viewYear) {
            return false;
        }
        const next = new Date(
            this.state.calendar.viewYear,
            this.state.calendar.viewMonth + 1,
            1
        );
        return next > new Date(today.getFullYear(), today.getMonth(), 1);
    }

    async chooseCalendarDate(day, event) {
        event?.preventDefault();
        event?.stopPropagation();
        if (day.future) return;
        this.closeDatePicker();
        await this.props.onChange(day.iso);
    }

    async chooseToday(event) {
        event?.preventDefault();
        event?.stopPropagation();
        if (!this.props.today) return;
        this.closeDatePicker();
        await this.props.onChange(this.props.today);
    }
}

export class ManualAttendanceDateTime extends Component {
    static template = "factory_attendance_dashboard.ManualAttendanceDateTime";
    static props = { value: String, defaultDate: String, caption: String, disabled: Boolean, onChange: Function, optional: { type: Boolean, optional: true } };
    setup() {
        this.root = useRef("timePicker");
        this.panel = useRef("timePanel");
        useEffect(() => { if (this.state.open) this.positionTimePanel(); }, () => [this.state.open]);
        useExternalListener(window, "resize", () => this.positionTimePanel());
        useExternalListener(document, "scroll", event => {
            if (this.state.open && !this.panel.el?.contains(event.target)) this.positionTimePanel();
        }, {capture: true});
        this.hours = Array.from({length: 12}, (_, i) => i + 1);
        this.minutes = Array.from({length: 60}, (_, i) => String(i).padStart(2, "0"));
        this.state = useState({time: (this.props.value.split("T")[1] || "").slice(0, 5), open: false, hour: 9, minute: "00", period: "AM"});
        onWillUpdateProps(next => {
            if (next.value !== this.props.value || next.defaultDate !== this.props.defaultDate) {
                this.state.time = (next.value.split("T")[1] || "").slice(0, 5);
                this.state.open = false;
            }
            if (next.disabled) this.state.open = false;
        });
        useExternalListener(document, "click", event => {
            if (!this.root.el?.contains(event.target) && !this.panel.el?.contains(event.target)) this.state.open = false;
        });
        useExternalListener(document, "keydown", event => {
            if (event.key === "Escape" && this.state.open) {
                event.stopPropagation();
                this.closeTime();
            }
        });
    }
    onTime(event) {
        const value = event.target.value.trim().replace(/[٠-٩]/g, digit => "٠١٢٣٤٥٦٧٨٩".indexOf(digit));
        const match = value.match(/^(\d{1,2}):(\d{2})$/);
        if (value && (!match || Number(match[1]) > 23 || Number(match[2]) > 59)) {
            event.target.value = this.state.time;
            return;
        }
        this.commit(value ? `${match[1].padStart(2, "0")}:${match[2]}` : "");
    }
    commit(time) {
        if (this.props.disabled) return;
        this.state.time = time;
        this.props.onChange(time ? `${this.props.defaultDate}T${time}` : "");
    }
    openTime() {
        if (this.props.disabled) return;
        if (this.state.open) { this.closeTime(); return; }
        const [hour, minute] = (this.state.time || "09:00").split(":");
        Object.assign(this.state, {hour: Number(hour) % 12 || 12, minute, period: Number(hour) >= 12 ? "PM" : "AM", open: true});
    }
    positionTimePanel() {
        const panel = this.panel.el;
        const anchor = this.root.el?.querySelector(".o_factory_time_input");
        if (!this.state.open || !panel || !anchor) return;
        const rect = anchor.getBoundingClientRect();
        const viewport = window.visualViewport;
        const width = viewport?.width || window.innerWidth;
        const height = viewport?.height || window.innerHeight;
        const inset = 12;
        panel.style.width = `${Math.min(350, width - inset * 2)}px`;
        panel.style.maxHeight = `${height - inset * 2}px`;
        const panelHeight = panel.offsetHeight;
        const below = height - rect.bottom - inset;
        const above = rect.top - inset;
        const top = below >= panelHeight || below >= above ? rect.bottom + 8 : rect.top - panelHeight - 8;
        panel.style.left = `${Math.max(inset, Math.min(rect.right - panel.offsetWidth, width - panel.offsetWidth - inset))}px`;
        panel.style.top = `${Math.max(inset, Math.min(top, height - panelHeight - inset))}px`;
    }
    closeTime() {
        this.state.open = false;
        this.root.el?.querySelector(".o_factory_time_toggle")?.focus();
    }
    applyTime() {
        const hour = this.state.hour % 12 + (this.state.period === "PM" ? 12 : 0);
        this.commit(`${String(hour).padStart(2, "0")}:${this.state.minute}`);
        this.closeTime();
    }
    clear() {
        this.commit("");
        this.closeTime();
    }

}

export class FactoryAttendanceDashboard extends Component {
    static components = { ArabicAttendanceDatePicker, ManualAttendanceDateTime };
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            refreshing: false,
            savingAllowance: false,
            error: "",
            selectedDepartmentId: false,
            selectedStatus: "all",
            selectedPayBasis: "time",
            selectedDate: "",
            data: {
                date: "",
                date_label: "",
                today: "",
                server_time: "--:--",
                departments: [],
                employees: [],
                can_manage_manual_attendance: false,
                counts: {
                    all: 0,
                    at_work: 0,
                    checked_out: 0,
                    absent: 0,
                    not_started: 0,
                    leave: 0,
                    late: 0,
                    overtime: 0,
                },
            },
            manualDialog: {
                open: false,
                employeeId: false,
                employeeName: "",
                selectedDate: "",
                today: "",
                timezone: "",
                records: [],
                attendanceId: false,
                checkIn: "",
                checkOut: "",
                selectedIsStale: false,
                loading: false,
                processing: false,
                error: "",
            },
        });
        this.requestToken = 0;
        this.manualDialogToken = 0;
        // Keep the live dashboard following the server's current day.  A
        // deliberately selected historical day remains fixed until "today"
        // is selected again.
        this.followToday = true;
        this.refreshTimer = null;
        this.statusRefreshTimer = null;
        onWillStart(() => this.loadDashboard());
        onMounted(() => {
            document.body.classList.add("o_factory_attendance_active");
            this.refreshTimer = setInterval(
                () => this.loadDashboard({ silent: true }),
                REFRESH_INTERVAL
            );
        });
        onWillDestroy(() => {
            document.body.classList.remove("o_factory_attendance_active");
            this.requestToken += 1;
            if (this.refreshTimer) {
                clearInterval(this.refreshTimer);
            }
            clearTimeout(this.statusRefreshTimer);
        });
    }

    async toggleQuarterAllowance() {
        if (this.state.savingAllowance || this.state.refreshing || !this.state.data.can_configure_absence_allowance) return;
        this.state.savingAllowance = true;
        try {
            const result = await this.orm.call('hr.employee', 'factory_set_quarter_absence_enabled', [!this.state.data.quarter_absence_enabled]);
            this.state.data.quarter_absence_enabled = result.enabled;
            this.notification.add(result.enabled ? 'تم تفعيل سماح 4 أيام وتحديث مسودات المرتبات.' : 'تم إلغاء سماح 4 أيام وتحديث مسودات المرتبات.', {type: 'success'});
        } catch (error) {
            this.notification.add(error.data?.message || error.message, {type: 'danger'});
        } finally { this.state.savingAllowance = false; }
    }

    async selectPayBasis(basis) {
        if (this.state.refreshing || this.state.selectedPayBasis === basis) return;
        this.state.selectedPayBasis = basis;
        this.state.selectedStatus = "all";
        await this.loadDashboard();
    }

    async loadDashboard({ silent = false } = {}) {
        if (this.state.refreshing || this.state.savingAllowance) {
            return;
        }
        const token = ++this.requestToken;
        this.state.refreshing = true;
        if (!silent) {
            this.state.loading = true;
        }
        this.state.error = "";
        try {
            const data = await this.orm.call(
                "hr.employee",
                "factory_attendance_dashboard_data",
                [
                    this.state.selectedDepartmentId || false,
                    this.state.selectedStatus || "all",
                    this.followToday ? false : (this.state.selectedDate || false),
                    this.state.selectedPayBasis,
                ]
            );
            if (token !== this.requestToken) {
                return;
            }
            this.state.data = data;
            this.state.selectedDepartmentId = data.selected_department_id || false;
            this.state.selectedStatus = data.selected_status || "all";
            this.state.selectedDate = data.date || "";
            this.scheduleStatusRefresh(data.next_status_change_delay_ms);
        } catch (error) {
            if (token !== this.requestToken) {
                return;
            }
            this.state.error =
                error.data?.message || error.message || "تعذر تحميل بيانات الحضور.";
            if (!silent) {
                this.notification.add(this.state.error, { type: "danger" });
            }
        } finally {
            if (token === this.requestToken) {
                this.state.loading = false;
                this.state.refreshing = false;
            }
        }
    }

    scheduleStatusRefresh(delayMs) {
        clearTimeout(this.statusRefreshTimer);
        this.statusRefreshTimer = null;
        if (Number.isFinite(delayMs) && delayMs > 0) {
            // Ask the server at shift start; do not invent client-side absence.
            this.statusRefreshTimer = setTimeout(() => {
                this.statusRefreshTimer = null;
                this.loadDashboard({ silent: true });
            }, Math.min(2147483647, Math.max(50, delayMs)));
        }
    }

    async onDepartmentChange(event) {
        this.state.selectedDepartmentId = Number(event.target.value || 0) || false;
        await this.loadDashboard();
    }

    async onStatusChange(event) {
        this.state.selectedStatus = event.target.value || "all";
        await this.loadDashboard();
    }

    async onCalendarDateChanged(value) {
        this.state.selectedDate = value;
        this.followToday = value === this.state.data.today;
        await this.loadDashboard();
    }

    async selectStatus(status) {
        if (status === this.state.selectedStatus) {
            return;
        }
        this.state.selectedStatus = status;
        await this.loadDashboard();
    }

    async refresh() {
        await this.loadDashboard();
    }

    async openManualAttendanceDialog(employee) {
        if (!employee.manual_attendance_enabled || this.state.manualDialog.processing) {
            return;
        }
        Object.assign(this.state.manualDialog, {
            open: true,
            employeeId: employee.id,
            employeeName: employee.name,
            selectedDate: this.state.selectedDate || this.state.data.date,
            today: this.state.data.today,
            timezone: "",
            records: [],
            attendanceId: false,
            checkIn: "",
            checkOut: "",
            selectedIsStale: false,
            loading: true,
            processing: false,
            error: "",
        });
        await this.loadManualAttendanceDetails();
    }

    async loadManualAttendanceDetails() {
        const dialog = this.state.manualDialog;
        const employeeId = dialog.employeeId;
        const token = ++this.manualDialogToken;
        dialog.loading = true;
        dialog.error = "";
        try {
            const details = await this.orm.call(
                "hr.employee",
                "factory_manual_attendance_details",
                [employeeId, dialog.selectedDate || false]
            );
            if (token !== this.manualDialogToken || !dialog.open) {
                return;
            }
            Object.assign(dialog, {
                employeeName: details.employee_name || dialog.employeeName,
                selectedDate: details.selected_date,
                today: details.today,
                timezone: details.timezone || "",
                records: details.records || [],
                attendanceId: details.default_attendance_id || false,
            });
            this.applyManualAttendanceRecord(dialog.attendanceId);
        } catch (error) {
            if (token === this.manualDialogToken && dialog.open) {
                dialog.error = error.data?.message || error.message ||
                    "تعذر تحميل سجلات الحضور للموظف.";
            }
        } finally {
            if (token === this.manualDialogToken) {
                dialog.loading = false;
            }
        }
    }

    applyManualAttendanceRecord(attendanceId) {
        const dialog = this.state.manualDialog;
        const id = Number(attendanceId || 0) || false;
        const record = dialog.records.find((item) => item.id === id);
        Object.assign(dialog, {
            attendanceId: id,
            checkIn: record?.check_in || "",
            checkOut: record?.check_out || "",
            selectedIsStale: Boolean(record?.is_stale),
            error: "",
        });
    }

    async onManualDateChange(value) {
        if (this.state.manualDialog.processing || this.state.manualDialog.loading) return;
        this.state.manualDialog.selectedDate = value || "";
        this.state.manualDialog.records = [];
        this.applyManualAttendanceRecord(false);
        await this.loadManualAttendanceDetails();
    }

    onManualRecordChange(event) {
        this.applyManualAttendanceRecord(event.target.value);
    }

    onManualCheckInInput(value) {
        this.state.manualDialog.checkIn = value;
        this.state.manualDialog.error = "";
    }

    onManualCheckOutInput(value) {
        this.state.manualDialog.checkOut = value;
        this.state.manualDialog.error = "";
    }

    closeManualDialog() {
        if (this.state.manualDialog.processing) {
            return;
        }
        this.manualDialogToken += 1;
        this.state.manualDialog.open = false;
    }

    async saveManualAttendance() {
        const dialog = this.state.manualDialog;
        if (dialog.processing || dialog.loading) {
            return;
        }
        if (!dialog.checkIn) {
            dialog.error = "اختَر تاريخ ووقت الحضور.";
            return;
        }
        dialog.processing = true;
        dialog.error = "";
        try {
            const result = await this.orm.call(
                "hr.employee",
                "factory_save_manual_attendance",
                [
                    dialog.employeeId,
                    dialog.attendanceId || false,
                    dialog.checkIn,
                    dialog.checkOut || false,
                ]
            );
            this.notification.add(
                result.action === "created"
                    ? `تم إنشاء سجل حضور ${result.employee_name} بنجاح.`
                    : `تم تحديث حضور ${result.employee_name} بنجاح.`,
                { type: "success" }
            );
            dialog.open = false;
            await this.loadDashboard({ silent: true });
        } catch (error) {
            dialog.error = error.data?.message || error.message ||
                "تعذر حفظ مواعيد الحضور والانصراف.";
        } finally {
            dialog.processing = false;
        }
    }

    statusLabel(status) {
        return {
            at_work: "حاضر",
            checked_out: "انصرف",
            absent: "غياب",
            not_started: "—",
            leave: "إجازة",
        }[status] || status;
    }
}

FactoryAttendanceDashboard.template =
    "factory_attendance_dashboard.FactoryAttendanceDashboard";

registry
    .category("actions")
    .add("factory_attendance_dashboard.dashboard", FactoryAttendanceDashboard);
