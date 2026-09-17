# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta

from pytz import timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .hr_employee_compensation import (
    FURNITURE_LATE_GRACE_MINUTES,
    FURNITURE_PAY_BASIS_SELECTION,
)


class SimplePayrollSlipFurniture(models.Model):
    """Factory payroll policy layered on the installed simple payroll model.

    A full factory pay week is six scheduled days (Saturday through Thursday).
    Time wages and monthly allowances are therefore divided by four for a full
    week, then prorated by scheduled days for shorter/longer selected periods.
    """

    _inherit = 'simple.payroll.slip'

    date_from = fields.Date(
        string='من تاريخ', required=True,
        default=lambda self: self.env[
            'hr.employee'
        ]._furniture_week_bounds()[0],
    )
    date_to = fields.Date(
        string='إلى تاريخ', required=True,
        default=lambda self: self.env[
            'hr.employee'
        ]._furniture_week_bounds()[1],
    )
    furniture_pay_basis = fields.Selection(
        FURNITURE_PAY_BASIS_SELECTION,
        string='طريقة حساب الأجر',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_piece_rate = fields.Monetary(
        string='سعر القطعة', currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_completed_piece_qty = fields.Integer(
        string='القطع المكتملة في الفترة',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_piece_earning = fields.Monetary(
        string='استحقاق الإنتاج', currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_monthly_allowance_total = fields.Monetary(
        string='البدلات الشهرية بالعقد',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_contract_allowance_ids = fields.One2many(
        related='contract_id.furniture_allowance_line_ids',
        string='بنود البدلات في العقد', readonly=True,
    )
    furniture_food_allowance_period = fields.Monetary(
        string='بدل الأكل للفترة',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_transport_allowance_period = fields.Monetary(
        string='بدل المواصلات للفترة',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_basic_allowance_period = fields.Monetary(
        string='البدل الأساسي للفترة',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_actual_late_hours = fields.Float(
        string='التأخير الفعلي',
        compute='_compute_amounts', store=True, readonly=True,
        digits=(16, 3),
    )
    factory_overtime_hours = fields.Float(
        string='ساعات العمل الإضافي',
        compute='_compute_amounts', store=True, readonly=True,
        digits=(16, 2),
        help=(
            'إجمالي الحضور المغلق بعد نهاية الوردية اليومية في أيام العمل '
            'المجدولة، وتُضاف قيمته للراتب آليًا حسب معامل العقد.'
        ),
    )
    furniture_overtime_multiplier = fields.Float(
        string='معامل الإضافي',
        compute='_compute_amounts', store=True, readonly=True,
        digits=(16, 2), aggregator='avg',
    )
    furniture_regular_hour_rate = fields.Monetary(
        string='سعر الساعة العادية',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
        aggregator='avg',
    )
    furniture_overtime_hour_rate = fields.Monetary(
        string='سعر ساعة الإضافي',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
        aggregator='avg',
    )
    furniture_overtime_amount = fields.Monetary(
        string='قيمة العمل الإضافي',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_late_grace_minutes = fields.Integer(
        string='دقائق السماح', default=FURNITURE_LATE_GRACE_MINUTES,
        required=True, readonly=True,
    )
    furniture_department_id = fields.Many2one(
        'hr.department', string='القسم',
        related='employee_id.department_id', store=True, readonly=True,
        index=True,
    )
    furniture_total_earnings = fields.Monetary(
        string='إجمالي الاستحقاقات',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )
    furniture_total_deductions = fields.Monetary(
        string='إجمالي الخصومات',
        currency_field='company_currency_id',
        compute='_compute_amounts', store=True, readonly=True,
    )

    @api.depends('employee_id', 'date_from')
    def _compute_name(self):
        for slip in self:
            if slip.employee_id and slip.date_from:
                slip.name = 'كشف أجر - %s - %s' % (
                    slip.employee_id.display_name,
                    fields.Date.to_string(slip.date_from),
                )
            else:
                slip.name = 'كشف أجر جديد'

    @api.constrains('date_from', 'date_to')
    def _check_furniture_payroll_period(self):
        for slip in self:
            if slip.date_from and slip.date_to and slip.date_to < slip.date_from:
                raise ValidationError(_('تاريخ نهاية كشف الأجر يسبق تاريخ البداية.'))

    @api.depends(
        'date_from',
        'date_to',
        'contract_id',
        'contract_id.resource_calendar_id',
        'contract_id.resource_calendar_id.hours_per_day',
        'contract_id.resource_calendar_id.tz',
        'contract_id.resource_calendar_id.attendance_ids.dayofweek',
        'contract_id.resource_calendar_id.attendance_ids.hour_from',
        'contract_id.resource_calendar_id.attendance_ids.hour_to',
        'contract_id.resource_calendar_id.attendance_ids.day_period',
        'contract_id.resource_calendar_id.attendance_ids.date_from',
        'contract_id.resource_calendar_id.attendance_ids.date_to',
        'employee_id.resource_calendar_id',
        'company_id.resource_calendar_id',
    )
    def _compute_work_schedule(self):
        """Count the selected week, not the administrative contract start.

        Contracts were created for existing workers during the week.  Limiting
        the calendar to that creation date incorrectly turned a Saturday-Friday
        payroll week into four days.  The selected period and the worker's
        calendar are the payroll source of truth.
        """
        for slip in self:
            slip.expected_working_days = 0.0
            slip.hours_per_day = 0.0
            if not slip.date_from or not slip.date_to:
                continue
            calendar = (
                slip.contract_id.resource_calendar_id
                or slip.employee_id.resource_calendar_id
                or slip.company_id.resource_calendar_id
            )
            if not calendar:
                continue
            calendar_tz = timezone(calendar.tz or 'UTC')
            period_start = calendar_tz.localize(
                datetime.combine(slip.date_from, time.min)
            )
            period_stop = calendar_tz.localize(
                datetime.combine(
                    slip.date_to + timedelta(days=1), time.min,
                )
            )
            work_data = calendar.get_work_duration_data(
                period_start,
                period_stop,
                compute_leaves=False,
            )
            slip.expected_working_days = work_data.get('days', 0.0)
            slip.hours_per_day = calendar.hours_per_day or 0.0

    @api.depends(
        'employee_id',
        'employee_id.furniture_pay_basis',
        'employee_id.furniture_piece_rate',
        'employee_id.attendance_ids.check_in',
        'employee_id.attendance_ids.check_out',
        'contract_id',
        'contract_id.wage',
        'contract_id.furniture_total_monthly_allowances',
        'contract_id.furniture_food_allowance',
        'contract_id.furniture_transport_allowance',
        'contract_id.furniture_basic_allowance',
        'contract_id.furniture_overtime_multiplier',
        'date_from',
        'date_to',
        'expected_working_days',
        'hours_per_day',
        'company_id',
        'factory_bonus_amount',
        'factory_other_deduction_amount',
    )
    def _compute_amounts(self):
        for slip in self:
            employee = slip.employee_id
            contract = slip.contract_id
            pay_basis = (
                (employee.furniture_pay_basis or 'time')
                if employee else 'time'
            )
            monthly_wage = (contract.wage or 0.0) if contract else 0.0
            weekly_wage = monthly_wage / 4.0
            expected_days = slip.expected_working_days or 0.0
            period_week_factor = expected_days / 6.0
            hours_per_day = slip.hours_per_day or 10.0
            monthly_allowances = (
                contract.furniture_total_monthly_allowances or 0.0
                if contract else 0.0
            )
            period_allowances = (
                monthly_allowances / 4.0
            ) * period_week_factor
            food_allowance = (
                (contract.furniture_food_allowance or 0.0) / 4.0
                * period_week_factor
            ) if contract else 0.0
            transport_allowance = (
                (contract.furniture_transport_allowance or 0.0) / 4.0
                * period_week_factor
            ) if contract else 0.0
            basic_allowance = (
                (contract.furniture_basic_allowance or 0.0) / 4.0
                * period_week_factor
            ) if contract else 0.0

            attendance = {
                'worked_hours': 0.0,
                'absence_days': 0.0,
                'late_hours': 0.0,
                'deductible_late_hours': 0.0,
                'overtime_hours': 0.0,
            }
            piece_summary = {
                'piece_qty': 0,
                'amount': 0.0,
            }
            if employee and slip.date_from and slip.date_to:
                attendance = employee._furniture_attendance_details(
                    slip.date_from, slip.date_to,
                )
                piece_summary = employee._furniture_piece_pay_details(
                    slip.date_from, slip.date_to,
                )

            overtime = {
                'hours': 0.0,
                'multiplier': (
                    contract.furniture_overtime_multiplier or 1.0
                    if contract else 1.0
                ),
                'regular_hour_rate': 0.0,
                'overtime_hour_rate': 0.0,
                'amount': 0.0,
            }
            if employee and slip.date_from and slip.date_to:
                overtime = employee._furniture_overtime_pay_details(
                    slip.date_from,
                    slip.date_to,
                    contract=contract,
                    attendance=attendance,
                )

            factory_metrics = slip._factory_payroll_attendance_metrics()
            expected_hours = expected_days * hours_per_day
            paid_leave_hours = min(
                factory_metrics['paid_leave_hours'], expected_hours,
            )
            paid_leave_days = (
                paid_leave_hours / hours_per_day if hours_per_day else 0.0
            )

            if pay_basis == 'production':
                base_wage = piece_summary['amount']
                absence_deduction = 0.0
                delay_deduction = 0.0
            else:
                base_wage = weekly_wage * period_week_factor
                day_rate = weekly_wage / 6.0
                hour_rate = weekly_wage / (6.0 * hours_per_day)
                absence_deduction = (
                    attendance.get('deductible_absence_days', attendance['absence_days']) * day_rate
                )
                delay_deduction = (
                    attendance['deductible_late_hours'] * hour_rate
                )

            # A period cannot lose more than its base wage.  Keep the two
            # displayed deduction rows aligned with the capped total as well.
            absence_deduction = min(absence_deduction, base_wage)
            delay_deduction = min(
                delay_deduction,
                max(base_wage - absence_deduction, 0.0),
            )
            attendance_deduction = absence_deduction + delay_deduction
            bonus = slip.factory_bonus_amount or 0.0
            other_deduction = slip.factory_other_deduction_amount or 0.0

            slip.furniture_pay_basis = pay_basis
            slip.furniture_piece_rate = (
                (employee.furniture_piece_rate or 0.0)
                if employee else 0.0
            )
            slip.furniture_completed_piece_qty = piece_summary['piece_qty']
            slip.furniture_piece_earning = piece_summary['amount']
            slip.furniture_monthly_allowance_total = monthly_allowances
            slip.furniture_food_allowance_period = food_allowance
            slip.furniture_transport_allowance_period = transport_allowance
            slip.furniture_basic_allowance_period = basic_allowance
            slip.furniture_actual_late_hours = attendance['late_hours']
            slip.base_wage = base_wage
            slip.contract_allowances = period_allowances
            slip.expected_hours = expected_hours
            slip.attendance_hours = attendance['worked_hours']
            slip.factory_attendance_days = factory_metrics['attendance_days']
            slip.factory_paid_leave_hours = paid_leave_hours
            slip.factory_paid_leave_days = paid_leave_days
            slip.factory_overtime_hours = overtime['hours']
            slip.furniture_overtime_multiplier = overtime['multiplier']
            slip.furniture_regular_hour_rate = overtime['regular_hour_rate']
            slip.furniture_overtime_hour_rate = overtime[
                'overtime_hour_rate'
            ]
            slip.furniture_overtime_amount = overtime['amount']
            slip.factory_biometric_attendance_count = factory_metrics[
                'biometric_count'
            ]
            slip.absence_days = attendance['absence_days']
            slip.absence_deduction = absence_deduction
            slip.delay_hours = attendance['deductible_late_hours']
            slip.delay_deduction = delay_deduction
            slip.attendance_deduction = attendance_deduction
            slip.commission_amount = bonus
            slip.furniture_total_earnings = (
                base_wage + period_allowances + bonus + overtime['amount']
            )
            slip.furniture_total_deductions = (
                attendance_deduction + other_deduction
            )
            slip.net_salary = max(
                base_wage
                + period_allowances
                + bonus
                + overtime['amount']
                - attendance_deduction
                - other_deduction,
                0.0,
            )

    def action_recalculate_furniture_payroll(self):
        self._compute_contract_id()
        self._compute_work_schedule()
        self._compute_amounts()
        self.write({
            'factory_last_calculated_at': fields.Datetime.now(),
            'factory_last_calculated_by_id': self.env.user.id,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}
