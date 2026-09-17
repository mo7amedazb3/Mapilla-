# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SimplePayrollSlip(models.Model):
    _inherit = "simple.payroll.slip"

    factory_attendance_days = fields.Float(
        string="أيام الحضور الفعلية",
        compute="_compute_amounts",
        store=True,
        readonly=True,
    )
    factory_paid_leave_days = fields.Float(
        string="أيام الإجازة المدفوعة",
        compute="_compute_amounts",
        store=True,
        readonly=True,
    )
    factory_paid_leave_hours = fields.Float(
        string="ساعات الإجازة المدفوعة",
        compute="_compute_amounts",
        store=True,
        readonly=True,
    )
    factory_overtime_hours = fields.Float(
        string="ساعات إضافية",
        compute="_compute_amounts",
        store=True,
        readonly=True,
        help="للمراجعة فقط؛ لا تُضاف قيمتها للراتب آليًا دون اعتماد حافز.",
    )
    factory_biometric_attendance_count = fields.Integer(
        string="حركات بصمة محسوبة",
        compute="_compute_amounts",
        store=True,
        readonly=True,
    )
    factory_bonus_amount = fields.Monetary(
        string="حوافز ومكافآت",
        currency_field="company_currency_id",
        default=0.0,
        copy=False,
    )
    factory_other_deduction_amount = fields.Monetary(
        string="خصومات أخرى",
        currency_field="company_currency_id",
        default=0.0,
        copy=False,
    )
    factory_net_after_due_advance = fields.Monetary(
        string="صافي تقديري بعد قسط السلفة",
        currency_field="company_currency_id",
        compute="_compute_factory_net_after_due_advance",
        readonly=True,
        help="عرض تقديري فقط؛ تسجيل سداد السلفة يظل من شاشة السلف.",
    )
    factory_last_calculated_at = fields.Datetime(
        string="آخر تحديث للحساب",
        readonly=True,
        copy=False,
    )
    factory_last_calculated_by_id = fields.Many2one(
        "res.users",
        string="تم التحديث بواسطة",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    @api.depends("employee_id", "date_from", "date_to", "company_id")
    def _compute_contract_id(self):
        Contract = self.env["hr.contract"].sudo()
        for slip in self:
            slip.contract_id = False
            if not slip.employee_id or not slip.date_from or not slip.date_to:
                continue
            contract = Contract.search(
                [
                    ("employee_id", "=", slip.employee_id.id),
                    ("company_id", "=", slip.company_id.id),
                    ("state", "in", ["open", "close"]),
                    ("date_start", "<=", slip.date_to),
                    "|",
                    ("date_end", "=", False),
                    ("date_end", ">=", slip.date_from),
                ],
                order="date_start desc, id desc",
                limit=1,
            )
            slip.contract_id = contract

    @api.depends("net_salary", "due_advance_installments")
    def _compute_factory_net_after_due_advance(self):
        for slip in self:
            slip.factory_net_after_due_advance = max(
                (slip.net_salary or 0.0) - (slip.due_advance_installments or 0.0),
                0.0,
            )

    def _factory_payroll_period_bounds(self):
        self.ensure_one()
        timezone_name = (
            self.employee_id._get_tz()
            or self.contract_id.resource_calendar_id.tz
            or self.env.user.tz
            or "Africa/Cairo"
        )
        try:
            zone = pytz.timezone(timezone_name)
        except pytz.UnknownTimeZoneError:
            zone = pytz.timezone("Africa/Cairo")
        local_start = zone.localize(datetime.combine(self.date_from, time.min))
        local_end = zone.localize(
            datetime.combine(self.date_to + timedelta(days=1), time.min)
        )
        return (
            zone,
            local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            local_end.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    def _factory_payroll_attendance_metrics(self):
        self.ensure_one()
        empty = {
            "attendance_hours": 0.0,
            "attendance_days": 0.0,
            "paid_leave_hours": 0.0,
            "biometric_count": 0,
        }
        if not self.employee_id or not self.date_from or not self.date_to:
            return empty

        zone, period_start, period_end = self._factory_payroll_period_bounds()
        attendances = self.env["hr.attendance"].sudo().search(
            [
                ("employee_id", "=", self.employee_id.id),
                ("check_in", "<", period_end),
                ("check_out", "!=", False),
                ("check_out", ">", period_start),
            ],
            order="check_in, id",
        )
        attendance_hours = 0.0
        attendance_dates = set()
        biometric_count = 0
        for attendance in attendances:
            clipped_start = max(attendance.check_in, period_start)
            clipped_end = min(attendance.check_out, period_end)
            if clipped_end <= clipped_start:
                continue
            attendance_hours += (
                clipped_end - clipped_start
            ).total_seconds() / 3600.0
            local_check_in = pytz.UTC.localize(attendance.check_in).astimezone(zone)
            attendance_dates.add(local_check_in.date())
            if (
                attendance.in_mode == "biometric"
                or attendance.out_mode == "biometric"
                or attendance.biometric_in_device_id
                or attendance.biometric_out_device_id
            ):
                biometric_count += 1

        # Lunch recognition is optional. Pay the confirmed break once without
        # modifying the source biometric/attendance intervals.
        if hasattr(self.employee_id, "_factory_paid_lunch_hours"):
            attendance_hours += self.employee_id._factory_paid_lunch_hours(
                period_start, period_end, zone=zone,
            )

        paid_leave_hours = 0.0
        calendar = (
            self.contract_id.resource_calendar_id
            or self.employee_id.resource_calendar_id
            or self.company_id.resource_calendar_id
        )
        if calendar:
            paid_leaves = self.env["hr.leave"].sudo().search(
                [
                    ("employee_id", "=", self.employee_id.id),
                    ("state", "=", "validate"),
                    ("holiday_status_id.unpaid", "=", False),
                    ("date_from", "<", period_end),
                    ("date_to", ">", period_start),
                ]
            )
            for leave in paid_leaves:
                clipped_start = max(leave.date_from, period_start)
                clipped_end = min(leave.date_to, period_end)
                if clipped_end <= clipped_start:
                    continue
                duration = calendar.get_work_duration_data(
                    pytz.UTC.localize(clipped_start),
                    pytz.UTC.localize(clipped_end),
                    compute_leaves=False,
                )
                paid_leave_hours += duration.get("hours", 0.0)

        return {
            "attendance_hours": round(attendance_hours, 4),
            "attendance_days": float(len(attendance_dates)),
            "paid_leave_hours": round(paid_leave_hours, 4),
            "biometric_count": biometric_count,
        }

    @api.depends(
        "employee_id",
        "contract_id",
        "date_from",
        "date_to",
        "expected_working_days",
        "hours_per_day",
        "company_id",
        "factory_bonus_amount",
        "factory_other_deduction_amount",
    )
    def _compute_amounts(self):
        for slip in self:
            wage = (
                slip.contract_id.wage
                if slip.contract_id and "wage" in slip.contract_id._fields
                else 0.0
            )
            allowances = (
                slip.contract_id.total_allowances
                if slip.contract_id
                and "total_allowances" in slip.contract_id._fields
                else 0.0
            )
            expected_hours = (
                (slip.expected_working_days or 0.0)
                * (slip.hours_per_day or 0.0)
            )
            metrics = slip._factory_payroll_attendance_metrics()
            actual_hours = metrics["attendance_hours"]
            paid_leave_hours = min(
                metrics["paid_leave_hours"], expected_hours
            )
            credited_hours = min(
                expected_hours, actual_hours + paid_leave_hours
            )
            missing_hours = max(expected_hours - credited_hours, 0.0)
            paid_leave_days = (
                paid_leave_hours / slip.hours_per_day
                if slip.hours_per_day
                else 0.0
            )
            absent_days = max(
                (slip.expected_working_days or 0.0)
                - metrics["attendance_days"]
                - paid_leave_days,
                0.0,
            )
            absent_hours = min(
                missing_hours,
                absent_days * (slip.hours_per_day or 0.0),
            )
            delay_hours = max(missing_hours - absent_hours, 0.0)
            gross_contract_salary = wage + allowances

            slip.base_wage = wage
            slip.contract_allowances = allowances
            slip.expected_hours = expected_hours
            slip.attendance_hours = actual_hours
            slip.factory_attendance_days = metrics["attendance_days"]
            slip.factory_paid_leave_hours = paid_leave_hours
            slip.factory_paid_leave_days = paid_leave_days
            slip.factory_overtime_hours = max(actual_hours - expected_hours, 0.0)
            slip.factory_biometric_attendance_count = metrics["biometric_count"]
            slip.absence_days = (
                absent_hours / slip.hours_per_day
                if slip.hours_per_day
                else 0.0
            )
            slip.delay_hours = delay_hours
            if expected_hours:
                slip.absence_deduction = (
                    absent_hours / expected_hours
                ) * gross_contract_salary
                slip.delay_deduction = (
                    delay_hours / expected_hours
                ) * gross_contract_salary
            else:
                slip.absence_deduction = 0.0
                slip.delay_deduction = 0.0
            slip.attendance_deduction = (
                slip.absence_deduction + slip.delay_deduction
            )
            slip.commission_amount = slip.factory_bonus_amount or 0.0
            slip.net_salary = max(
                gross_contract_salary
                + (slip.factory_bonus_amount or 0.0)
                - slip.attendance_deduction
                - (slip.factory_other_deduction_amount or 0.0),
                0.0,
            )

    @api.constrains("date_from", "date_to")
    def _check_factory_payroll_period(self):
        for slip in self:
            if slip.date_from and slip.date_to and slip.date_from > slip.date_to:
                raise ValidationError(_("تاريخ بداية المرتب يجب أن يسبق تاريخ النهاية."))

    @api.constrains("factory_bonus_amount", "factory_other_deduction_amount")
    def _check_factory_adjustments(self):
        for slip in self:
            if slip.factory_bonus_amount < 0 or slip.factory_other_deduction_amount < 0:
                raise ValidationError(_("الحوافز والخصومات الإضافية لا تقبل قيمة سالبة."))

    @api.constrains("employee_id", "company_id", "date_from", "date_to", "state")
    def _check_factory_duplicate_period(self):
        for slip in self.filtered(lambda item: item.state != "cancel"):
            duplicate = self.search_count(
                [
                    ("id", "!=", slip.id),
                    ("employee_id", "=", slip.employee_id.id),
                    ("company_id", "=", slip.company_id.id),
                    ("date_from", "=", slip.date_from),
                    ("date_to", "=", slip.date_to),
                    ("state", "!=", "cancel"),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _("يوجد بالفعل مرتب لنفس العامل ونفس الفترة.")
                )

    def action_recompute_factory_payroll(self):
        self._compute_contract_id()
        self._compute_work_schedule()
        self._compute_amounts()
        self.write(
            {
                "factory_last_calculated_at": fields.Datetime.now(),
                "factory_last_calculated_by_id": self.env.user.id,
            }
        )
        return True

    def action_confirm(self):
        for slip in self:
            if slip.state != "draft":
                continue
            slip.action_recompute_factory_payroll()
            if not slip.contract_id:
                raise UserError(
                    _("لا يمكن اعتماد مرتب %s بدون عقد يغطي الفترة.")
                    % slip.employee_id.display_name
                )
        return super().action_confirm()

    def action_paid(self):
        if any(slip.state != "confirmed" for slip in self):
            raise UserError(_("لا يمكن تسجيل الدفع قبل اعتماد المرتب."))
        return super().action_paid()

    def unlink(self):
        if any(slip.state not in ("draft", "cancel") for slip in self):
            raise UserError(_("يمكن حذف المرتبات المسودة أو الملغاة فقط."))
        return super().unlink()
