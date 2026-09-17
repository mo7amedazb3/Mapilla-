# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

from pytz import timezone

from odoo import api, fields, models, _


class SimplePayrollSlip(models.Model):
    _name = "simple.payroll.slip"
    _description = "Salary Slip"
    _order = "date_from desc, employee_id, id desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    name = fields.Char(string="Reference", compute="_compute_name", store=True)

    employee_id = fields.Many2one("hr.employee", required=True)
    contract_id = fields.Many2one("hr.contract", string="Contract", compute="_compute_contract_id", store=True, readonly=True)

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)

    expected_working_days = fields.Float(
        string="Expected Working Days",
        compute="_compute_work_schedule",
        store=True,
        readonly=True,
    )
    hours_per_day = fields.Float(
        string="Hours per Day",
        compute="_compute_work_schedule",
        store=True,
        readonly=True,
    )

    base_wage = fields.Monetary(string="Base Salary (Contract)", currency_field="company_currency_id",
                                compute="_compute_amounts", store=True)
    contract_allowances = fields.Monetary(string="Contract Allowances", currency_field="company_currency_id",
                                          compute="_compute_amounts", store=True)
    expected_hours = fields.Float(string="Expected Hours", compute="_compute_amounts", store=True)
    attendance_hours = fields.Float(string="Attendance Hours", compute="_compute_amounts", store=True)
    absence_days = fields.Float(string="Absence Days", compute="_compute_amounts", store=True)
    absence_deduction = fields.Monetary(string="Absence Deduction", currency_field="company_currency_id", compute="_compute_amounts", store=True)
    delay_hours = fields.Float(string="Delay Hours", compute="_compute_amounts", store=True)
    delay_deduction = fields.Monetary(string="Delay Deduction", currency_field="company_currency_id", compute="_compute_amounts", store=True)
    attendance_deduction = fields.Monetary(string="Total Attendance Deduction", currency_field="company_currency_id",
                                          compute="_compute_amounts", store=True)

    commission_amount = fields.Monetary(string="Commission", currency_field="company_currency_id",
                                       compute="_compute_amounts", store=True)

    net_salary = fields.Monetary(string="Net Salary", currency_field="company_currency_id",
                                 compute="_compute_amounts", store=True)

    company_currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)

    state = fields.Selection(
        selection=[("draft", "Draft"), ("confirmed", "Confirmed"), ("paid", "Paid"), ("cancel", "Cancelled")],
        default="draft",
        index=True,
        required=True,
    )

    @api.depends("employee_id", "date_from", "date_to")
    def _compute_name(self):
        for rec in self:
            if rec.employee_id and rec.date_from:
                month_year = rec.date_from.strftime("%B %Y")
                rec.name = f"Slip - {rec.employee_id.name} - {month_year}"
            else:
                rec.name = "New Slip"

    @api.depends("employee_id", "date_from", "date_to", "company_id")
    def _compute_contract_id(self):
        Contract = self.env["hr.contract"].sudo()
        for rec in self:
            rec.contract_id = False
            if not rec.employee_id or not rec.date_from or not rec.date_to:
                continue
            domain = [
                ("employee_id", "=", rec.employee_id.id),
                ("company_id", "=", rec.company_id.id),
                ("date_start", "<=", rec.date_to),
                "|", ("date_end", "=", False), ("date_end", ">=", rec.date_from),
            ]
            # Prefer open contract if state exists
            if "state" in Contract._fields:
                domain = domain + [("state", "in", ["open", "draft", "close"])]
            contract = Contract.search(domain, order="date_start desc, id desc", limit=1)
            rec.contract_id = contract.id if contract else False

    @api.depends(
        "date_from",
        "date_to",
        "contract_id",
        "contract_id.date_start",
        "contract_id.date_end",
        "contract_id.resource_calendar_id",
        "contract_id.resource_calendar_id.hours_per_day",
        "contract_id.resource_calendar_id.tz",
        "contract_id.resource_calendar_id.two_weeks_calendar",
        "contract_id.resource_calendar_id.flexible_hours",
        "contract_id.resource_calendar_id.full_time_required_hours",
        "contract_id.resource_calendar_id.attendance_ids.dayofweek",
        "contract_id.resource_calendar_id.attendance_ids.hour_from",
        "contract_id.resource_calendar_id.attendance_ids.hour_to",
        "contract_id.resource_calendar_id.attendance_ids.day_period",
        "contract_id.resource_calendar_id.attendance_ids.duration_days",
        "contract_id.resource_calendar_id.attendance_ids.week_type",
        "contract_id.resource_calendar_id.attendance_ids.date_from",
        "contract_id.resource_calendar_id.attendance_ids.date_to",
        "contract_id.resource_calendar_id.attendance_ids.resource_id",
        "contract_id.resource_calendar_id.attendance_ids.display_type",
    )
    def _compute_work_schedule(self):
        for rec in self:
            rec.expected_working_days = 0.0
            rec.hours_per_day = 0.0

            contract = rec.contract_id
            calendar = contract.resource_calendar_id if contract else False
            if not calendar or not rec.date_from or not rec.date_to:
                continue

            period_start = max(rec.date_from, contract.date_start)
            period_end = min(rec.date_to, contract.date_end or rec.date_to)
            if period_start > period_end:
                continue

            calendar_tz = timezone(calendar.tz or "UTC")
            start_dt = calendar_tz.localize(datetime.combine(period_start, time.min))
            end_dt = calendar_tz.localize(
                datetime.combine(period_end + timedelta(days=1), time.min)
            )

            # Calendar attendances are the source of truth. Days with no work
            # interval (for example Friday in a Saturday-Thursday schedule) are
            # not expected working days and therefore cannot become absences.
            work_data = calendar.get_work_duration_data(
                start_dt,
                end_dt,
                compute_leaves=False,
            )
            rec.expected_working_days = work_data.get("days", 0.0)
            rec.hours_per_day = calendar.hours_per_day or 0.0

    def _get_attendance_data(self, employee, date_from, date_to):
        Attendance = self.env["hr.attendance"].sudo()
        if not employee or not date_from or not date_to:
            return 0.0, 0
        # Use check_in range (approx) within [date_from, date_to + 1day)
        start_dt = datetime.combine(date_from, time.min)
        end_dt = datetime.combine(date_to + timedelta(days=1), time.min)
        domain = [
            ("employee_id", "=", employee.id),
            ("check_in", ">=", fields.Datetime.to_string(start_dt)),
            ("check_in", "<", fields.Datetime.to_string(end_dt)),
            ("check_out", "!=", False),
        ]
        recs = Attendance.search(domain)
        
        attended_dates = set()
        for r in recs:
            if r.check_in:
                attended_dates.add(r.check_in.date())
        days_attended = len(attended_dates)

        paid_lunch = (employee._factory_paid_lunch_hours(start_dt, end_dt)
                      if hasattr(employee, "_factory_paid_lunch_hours") else 0.0)
        if "worked_hours" in Attendance._fields:
            return sum(round(h, 2) for h in recs.mapped("worked_hours")) + paid_lunch, days_attended
        # fallback compute
        total = 0.0
        for r in recs:
            if r.check_in and r.check_out:
                delta = fields.Datetime.to_datetime(r.check_out) - fields.Datetime.to_datetime(r.check_in)
                total += round(delta.total_seconds() / 3600.0, 2)
        return total + paid_lunch, days_attended

    def _get_commission_amount(self, employee, date_from, date_to):
        # Read from new custom_spa_commission module
        try:
            CommissionResult = self.env["spa.commission.result"].sudo()
        except Exception:
            return 0.0
        if not employee or not date_from or not date_to:
            return 0.0
        
        domain = [
            ("specialist_id", "=", employee.id),
            ("invoice_date", ">=", date_from),
            ("invoice_date", "<=", date_to),
            ("period_id.state", "in", ["calculated", "approved"]),
        ]
        recs = CommissionResult.search(domain)
        if "commission_amount" in CommissionResult._fields:
            return sum(recs.mapped("commission_amount")) or 0.0
        return 0.0

    @api.depends("employee_id", "contract_id", "date_from", "date_to", "expected_working_days", "hours_per_day", "company_id")
    def _compute_amounts(self):
        for rec in self:
            wage = rec.contract_id.wage if rec.contract_id and "wage" in rec.contract_id._fields else 0.0
            allowances = rec.contract_id.total_allowances if rec.contract_id and "total_allowances" in rec.contract_id._fields else 0.0
            
            rec.base_wage = wage
            rec.contract_allowances = allowances

            expected_hours = (rec.expected_working_days or 0.0) * (rec.hours_per_day or 0.0)
            rec.expected_hours = expected_hours

            att_hours, days_attended = rec._get_attendance_data(rec.employee_id, rec.date_from, rec.date_to)
            rec.attendance_hours = att_hours

            total_missing_hours = max(0.0, expected_hours - att_hours)
            
            absent_days_calc = max(0.0, rec.expected_working_days - days_attended)
            absent_hours_calc = absent_days_calc * (rec.hours_per_day or 0.0)
            
            deductible_absent_hours = min(total_missing_hours, absent_hours_calc)
            delay_hours_calc = total_missing_hours - deductible_absent_hours
            
            rec.absence_days = deductible_absent_hours / rec.hours_per_day if rec.hours_per_day else 0.0
            rec.delay_hours = delay_hours_calc
            
            total_salary = wage + allowances
            if expected_hours > 0:
                rec.absence_deduction = (deductible_absent_hours / expected_hours) * total_salary
                rec.delay_deduction = (delay_hours_calc / expected_hours) * total_salary
            else:
                rec.absence_deduction = 0.0
                rec.delay_deduction = 0.0

            rec.attendance_deduction = rec.absence_deduction + rec.delay_deduction

            commission = rec._get_commission_amount(rec.employee_id, rec.date_from, rec.date_to)
            rec.commission_amount = commission

            rec.net_salary = rec.base_wage + rec.contract_allowances + rec.commission_amount - rec.attendance_deduction

    def action_confirm(self):
        for rec in self:
            rec.state = "confirmed"
        return True

    def action_paid(self):
        for rec in self:
            rec.state = "paid"
        return True

    def action_cancel(self):
        for rec in self:
            rec.state = "cancel"
        return True

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"
        return True
