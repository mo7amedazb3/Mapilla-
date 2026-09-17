# -*- coding: utf-8 -*-

from datetime import datetime, time, timedelta

from pytz import UTC, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .mrp_production_order import FURNITURE_STAGE_SELECTION


FURNITURE_PAY_BASIS_SELECTION = [
    ('time', 'بالوقت'),
    ('production', 'بالإنتاج / القطعة'),
]
FURNITURE_LATE_GRACE_MINUTES = 15


class HrEmployeeCompensation(models.Model):
    _inherit = 'hr.employee'

    furniture_pay_currency_id = fields.Many2one(
        related='company_id.currency_id',
        string='عملة الأجر',
        readonly=True,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )

    furniture_pay_basis = fields.Selection(
        FURNITURE_PAY_BASIS_SELECTION,
        string='طريقة حساب الأجر',
        required=True,
        default='time',
        tracking=True,
        index=True,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_piece_rate = fields.Monetary(
        string='أجر القطعة',
        currency_field='furniture_pay_currency_id',
        default=0.0,
        tracking=True,
        help='القيمة المستحقة للعامل عن كل قطعة مكتملة في تكليف MPS.',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_week_piece_qty = fields.Integer(
        string='قطع الأسبوع الحالي',
        compute='_compute_furniture_current_pay_snapshot',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_week_fixed_wage = fields.Monetary(
        string='الأجر الأسبوعي الثابت',
        currency_field='furniture_pay_currency_id',
        compute='_compute_furniture_current_pay_snapshot',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_week_pay = fields.Monetary(
        string='استحقاق الأسبوع الحالي',
        currency_field='furniture_pay_currency_id',
        compute='_compute_furniture_current_pay_snapshot',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_week_overtime_hours = fields.Float(
        string='ساعات إضافية هذا الأسبوع',
        compute='_compute_furniture_current_pay_snapshot',
        digits=(16, 2),
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_week_overtime_amount = fields.Monetary(
        string='قيمة الإضافي هذا الأسبوع',
        currency_field='furniture_pay_currency_id',
        compute='_compute_furniture_current_pay_snapshot',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_month_absence_days = fields.Integer(
        string='غياب الشهر الحالي',
        compute='_compute_furniture_current_pay_snapshot',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_current_month_late_hours = fields.Float(
        string='تأخير الشهر الحالي',
        compute='_compute_furniture_current_pay_snapshot',
        digits=(16, 2),
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )

    @api.constrains('furniture_piece_rate')
    def _check_furniture_piece_rate(self):
        for employee in self:
            if employee.furniture_piece_rate < 0:
                raise ValidationError(_('أجر القطعة لا يمكن أن يكون سالبًا.'))

    @api.model
    def _furniture_week_bounds(self, target_date=None):
        """Factory pay week is Saturday through Friday."""
        target_date = fields.Date.to_date(target_date or fields.Date.context_today(self))
        days_since_saturday = (target_date.weekday() - 5) % 7
        week_start = target_date - timedelta(days=days_since_saturday)
        return week_start, week_start + timedelta(days=6)

    def _furniture_contract_for_date(self, target_date):
        self.ensure_one()
        target_date = fields.Date.to_date(target_date)
        contracts = self.env['hr.contract'].sudo().search([
            ('employee_id', '=', self.id),
            ('state', 'in', ('open', 'draft', 'close')),
            ('date_start', '<=', target_date),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', target_date),
        ], order='date_start desc, id desc')
        running = contracts.filtered(lambda contract: contract.state == 'open')
        return running[:1] or contracts[:1]

    def _furniture_timezone(self, calendar=False):
        self.ensure_one()
        tz_name = (
            (calendar and calendar.tz)
            or self.resource_calendar_id.tz
            or self.env.user.tz
            or 'UTC'
        )
        return timezone(tz_name)

    def _furniture_period_utc(self, date_from, date_to, calendar=False):
        self.ensure_one()
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        tz = self._furniture_timezone(calendar)
        start_local = tz.localize(datetime.combine(date_from, time.min))
        stop_local = tz.localize(
            datetime.combine(date_to + timedelta(days=1), time.min)
        )
        return (
            start_local.astimezone(UTC).replace(tzinfo=None),
            stop_local.astimezone(UTC).replace(tzinfo=None),
        )

    def _furniture_piece_pay_details(self, date_from, date_to):
        self.ensure_one()
        start_utc, stop_utc = self._furniture_period_utc(date_from, date_to)
        assignments = self.env[
            'furniture.mrp.mps.assignment'
        ].sudo().search([
            ('employee_id', '=', self.id),
            ('state', '=', 'done'),
            ('compensation_basis_snapshot', '=', 'production'),
            ('completed_at', '>=', fields.Datetime.to_string(start_utc)),
            ('completed_at', '<', fields.Datetime.to_string(stop_utc)),
        ], order='completed_at, id')
        return {
            'assignments': assignments,
            'piece_qty': sum(assignments.mapped('piece_count')),
            'amount': sum(assignments.mapped('piece_earning')),
        }

    def _furniture_attendance_details(self, date_from, date_to):
        """Return scheduled-day attendance without applying pay deductions."""
        self.ensure_one()
        date_from = fields.Date.to_date(date_from)
        date_to = min(
            fields.Date.to_date(date_to),
            fields.Date.context_today(self),
        )
        empty = {
            'lines': [],
            'scheduled_days': 0,
            'attendance_days': 0,
            'absence_days': 0,
            'late_days': 0,
            'late_hours': 0.0,
            'deductible_late_hours': 0.0,
            'worked_hours': 0.0,
            'overtime_hours': 0.0,
        }
        if date_to < date_from or not self.resource_id:
            return empty

        first_contract = self._furniture_contract_for_date(date_from)
        default_calendar = (
            first_contract.resource_calendar_id
            or self.resource_calendar_id
            or self.company_id.resource_calendar_id
        )
        start_utc, stop_utc = self._furniture_period_utc(
            date_from, date_to, default_calendar,
        )
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', self.id),
            ('check_in', '<', fields.Datetime.to_string(stop_utc)),
            '|',
            ('check_out', '=', False),
            ('check_out', '>', fields.Datetime.to_string(start_utc)),
        ], order='check_in, id')
        now_utc = fields.Datetime.to_datetime(fields.Datetime.now())
        details = dict(empty)
        current_date = date_from
        while current_date <= date_to:
            contract = self._furniture_contract_for_date(current_date)
            calendar = (
                (contract and contract.resource_calendar_id)
                or self.resource_calendar_id
                or self.company_id.resource_calendar_id
            )
            if not calendar:
                current_date += timedelta(days=1)
                continue
            tz = self._furniture_timezone(calendar)
            day_start_local = tz.localize(
                datetime.combine(current_date, time.min)
            )
            day_stop_local = tz.localize(
                datetime.combine(current_date + timedelta(days=1), time.min)
            )
            day_start = day_start_local.astimezone(UTC)
            day_stop = day_stop_local.astimezone(UTC)
            work_intervals = calendar.sudo()._work_intervals_batch(
                day_start,
                day_stop,
                resources=self.resource_id.sudo(),
                tz=tz,
                compute_leaves=True,
            ).get(self.resource_id.id, ())
            if not work_intervals:
                current_date += timedelta(days=1)
                continue

            scheduled_start = min(interval[0] for interval in work_intervals)
            scheduled_stop = max(interval[1] for interval in work_intervals)
            scheduled_hours = sum(
                (interval[1] - interval[0]).total_seconds() / 3600.0
                for interval in work_intervals
            )
            day_attendances = []
            overtime_intervals = []
            worked_seconds = 0.0
            for attendance in attendances:
                check_in = fields.Datetime.to_datetime(
                    attendance.check_in
                ).replace(tzinfo=UTC)
                check_out = fields.Datetime.to_datetime(
                    attendance.check_out or now_utc
                ).replace(tzinfo=UTC)
                overlap_start = max(check_in, day_start)
                overlap_stop = min(check_out, day_stop)
                if overlap_stop <= overlap_start:
                    continue
                day_attendances.append((check_in, check_out))
                worked_seconds += (
                    overlap_stop - overlap_start
                ).total_seconds()

                # Overtime is deliberately independent from total worked
                # hours, early arrivals and lateness.  Only a closed
                # attendance interval after the final scheduled stop of a
                # scheduled day is payable.
                if attendance.check_out:
                    overtime_start = max(check_in, scheduled_stop, day_start)
                    overtime_stop = min(check_out, day_stop)
                    if overtime_stop > overtime_start:
                        overtime_intervals.append((
                            overtime_start, overtime_stop,
                        ))

            # hr.attendance normally prevents overlaps, but merge the
            # intervals defensively so duplicated biometric rows can never
            # pay the same minute twice.
            overtime_seconds = 0.0
            merged_overtime = []
            for interval_start, interval_stop in sorted(overtime_intervals):
                if (
                    merged_overtime
                    and interval_start <= merged_overtime[-1][1]
                ):
                    merged_overtime[-1] = (
                        merged_overtime[-1][0],
                        max(merged_overtime[-1][1], interval_stop),
                    )
                else:
                    merged_overtime.append((interval_start, interval_stop))
            overtime_seconds = sum(
                (interval_stop - interval_start).total_seconds()
                for interval_start, interval_stop in merged_overtime
            )

            # Confirmed lunch is paid attendance. Overtime still uses only
            # closed, physically worked intervals after the scheduled stop.
            paid_lunch_seconds = 0.0
            unpaid_lunch_seconds = 0.0
            raw_attendance_intervals = [
                (row.check_in, row.check_out) for row in attendances
            ]
            if hasattr(self, '_factory_paid_lunch_seconds'):
                paid_lunch_seconds = self._factory_paid_lunch_seconds(
                    raw_attendance_intervals,
                    current_date, tz, now=now_utc,
                )
            if hasattr(self, '_factory_unpaid_lunch_seconds'):
                unpaid_lunch_seconds = self._factory_unpaid_lunch_seconds(
                    raw_attendance_intervals,
                    current_date, tz, now=now_utc,
                )
            worked_hours = (worked_seconds + paid_lunch_seconds) / 3600.0
            overtime_hours = overtime_seconds / 3600.0
            if day_attendances:
                first_check_in = min(row[0] for row in day_attendances)
                late_seconds = max(
                    (first_check_in - scheduled_start).total_seconds(),
                    0.0,
                )
                late_hours = late_seconds / 3600.0
                # The first 15 minutes are a threshold, not a free slice.
                # Up to and including 15 minutes has no deduction.  Once the
                # threshold is crossed the full delay is deductible: a
                # 20-minute delay deducts 20 minutes, not only 5.
                arrival_delay_hours = (
                    late_hours
                    if late_seconds > FURNITURE_LATE_GRACE_MINUTES * 60
                    else 0.0
                )
                deductible_late_hours = (
                    arrival_delay_hours + unpaid_lunch_seconds / 3600.0
                )
                status = 'present'
                details['attendance_days'] += 1
                if deductible_late_hours:
                    details['late_days'] += 1
            else:
                late_hours = 0.0
                deductible_late_hours = 0.0
                status = 'absent'
                details['absence_days'] += 1

            details['scheduled_days'] += 1
            details['late_hours'] += late_hours
            details['deductible_late_hours'] += deductible_late_hours
            details['worked_hours'] += worked_hours
            details['overtime_hours'] += overtime_hours
            details['lines'].append({
                'date': current_date,
                'status': status,
                'scheduled_hours': scheduled_hours,
                'worked_hours': worked_hours,
                'paid_lunch_hours': paid_lunch_seconds / 3600.0,
                'lunch_delay_hours': unpaid_lunch_seconds / 3600.0,
                'late_hours': late_hours,
                'deductible_late_hours': deductible_late_hours,
                'overtime_hours': overtime_hours,
            })
            current_date += timedelta(days=1)
        if not self.env.context.get('factory_skip_quarter_absence'):
            self._furniture_apply_quarter_absence(details, date_from, date_to)
        return details

    def _furniture_apply_quarter_absence(self, details, date_from, date_to):
        """First four scheduled absences per calendar quarter, across all payslips.

        Derive usage from dated attendance rather than spending credits on each
        payroll calculation. Recomputing or splitting a payroll period is safe.
        Rest days and approved leave are excluded by the working calendar.
        """
        self.ensure_one()
        from datetime import date
        enabled = self.env['ir.config_parameter'].sudo().get_param(
            'factory_attendance.quarter_absence.%s' % self.company_id.id, 'True') == 'True'
        if not enabled:
            details['absence_allowance_days'] = 0
            details['deductible_absence_days'] = details['absence_days']
            for line in details['lines']:
                line['absence_allowance_day'] = False
            return
        waived = set()
        quarters = {(line['date'].year, (line['date'].month - 1) // 3)
                    for line in details['lines']}
        contracts = self.env['hr.contract'].sudo().search([
            ('employee_id', '=', self.id), ('state', 'in', ['open', 'draft', 'close'])
        ])
        # Without a contract, start at the employee's recorded joining date.
        fallback_start = fields.Date.to_date(self.create_date)
        for year, quarter in sorted(quarters):
            start = date(year, quarter * 3 + 1, 1)
            next_start = date(year + 1, 1, 1) if quarter == 3 else date(year, quarter * 3 + 4, 1)
            stop = min(date_to, next_start - timedelta(days=1))
            employed_starts = [max(start, c.date_start) for c in contracts
                               if c.date_start <= stop and (not c.date_end or c.date_end >= start)]
            scan_start = min(employed_starts) if employed_starts else max(start, fallback_start)
            if scan_start > stop:
                continue
            history = self.with_context(factory_skip_quarter_absence=True)._furniture_attendance_details(scan_start, stop)
            used = 0
            for line in history['lines']:
                day = line['date']
                employed = any(c.date_start <= day and (not c.date_end or day <= c.date_end) for c in contracts) if contracts else day >= fallback_start
                if employed and line['status'] == 'absent' and used < 4:
                    waived.add(day)
                    used += 1
        for line in details['lines']:
            line['absence_allowance_day'] = line['status'] == 'absent' and line['date'] in waived
        details['absence_allowance_days'] = sum(line['absence_allowance_day'] for line in details['lines'])
        details['deductible_absence_days'] = max(0, details['absence_days'] - details['absence_allowance_days'])

    def _furniture_overtime_pay_details(
        self, date_from, date_to, contract=False, attendance=False,
    ):
        """Return payable overtime using the contract's ordinary hour rate.

        Piece-paid workers still expose attendance overtime hours, but there
        is no ordinary hourly base to multiply, so their automatic overtime
        value stays zero until a time-based wage policy is selected.
        """
        self.ensure_one()
        contract = contract or self._furniture_contract_for_date(date_to)
        attendance = attendance or self._furniture_attendance_details(
            date_from, date_to,
        )
        multiplier = (
            contract.furniture_overtime_multiplier or 1.0
            if contract else 1.0
        )
        regular_hour_rate = 0.0
        if contract and self.furniture_pay_basis != 'production':
            calendar = (
                contract.resource_calendar_id
                or self.resource_calendar_id
                or self.company_id.resource_calendar_id
            )
            hours_per_day = (
                calendar.hours_per_day if calendar else 0.0
            ) or 10.0
            regular_hour_rate = (
                ((contract.wage or 0.0) / 4.0)
                / (6.0 * hours_per_day)
            )
        overtime_hours = attendance.get('overtime_hours', 0.0)
        overtime_hour_rate = regular_hour_rate * multiplier
        return {
            'hours': overtime_hours,
            'multiplier': multiplier,
            'regular_hour_rate': regular_hour_rate,
            'overtime_hour_rate': overtime_hour_rate,
            'amount': overtime_hours * overtime_hour_rate,
        }

    @api.depends(
        'furniture_pay_basis',
        'furniture_piece_rate',
        'contract_ids.wage',
        'contract_ids.state',
        'contract_ids.date_start',
        'contract_ids.date_end',
        'contract_ids.furniture_overtime_multiplier',
    )
    def _compute_furniture_current_pay_snapshot(self):
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        for employee in self:
            week_start, week_end = employee._furniture_week_bounds(today)
            contract = employee._furniture_contract_for_date(week_end)
            fixed_weekly_wage = (contract.wage or 0.0) / 4.0 if contract else 0.0
            piece_summary = employee._furniture_piece_pay_details(
                week_start, week_end,
            )
            week_attendance = employee._furniture_attendance_details(
                week_start, week_end,
            )
            overtime = employee._furniture_overtime_pay_details(
                week_start, week_end,
                contract=contract,
                attendance=week_attendance,
            )
            attendance = employee._furniture_attendance_details(
                month_start, today,
            )
            employee.furniture_current_week_piece_qty = (
                piece_summary['piece_qty']
            )
            employee.furniture_current_week_fixed_wage = fixed_weekly_wage
            employee.furniture_current_week_overtime_hours = overtime['hours']
            employee.furniture_current_week_overtime_amount = overtime['amount']
            employee.furniture_current_week_pay = (
                piece_summary['amount']
                if employee.furniture_pay_basis == 'production'
                else fixed_weekly_wage + overtime['amount']
            )
            employee.furniture_current_month_absence_days = attendance[
                'absence_days'
            ]
            employee.furniture_current_month_late_hours = attendance[
                'deductible_late_hours'
            ]

    def _check_furniture_pay_report_access(self):
        if self.env.su:
            return True
        if not (
            self.env.user.has_group(
                'furniture_mrp.group_furniture_mrp_manager'
            )
            or self.env.user.has_group('hr_contract.group_hr_contract_manager')
        ):
            raise AccessError(_(
                'كشف الأجر والحضور متاح لمدير المصنع أو مسؤول العقود فقط.'
            ))
        return True

    def action_open_furniture_pay_report(self):
        self.ensure_one()
        self._check_furniture_pay_report_access()
        wizard = self.env['furniture.mrp.employee.pay.report'].create({
            'employee_id': self.id,
        })
        wizard._refresh_report()
        return wizard._action_open()


class HrContractCompensation(models.Model):
    _inherit = 'hr.contract'

    furniture_pay_basis = fields.Selection(
        related='employee_id.furniture_pay_basis',
        string='طريقة حساب الأجر',
        readonly=False,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_piece_rate = fields.Monetary(
        related='employee_id.furniture_piece_rate',
        string='أجر القطعة',
        currency_field='currency_id',
        readonly=False,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_weekly_wage = fields.Monetary(
        string='الأجر الأسبوعي',
        currency_field='currency_id',
        compute='_compute_furniture_weekly_wage',
        inverse='_inverse_furniture_weekly_wage',
        help='يعرض راتب Odoo الشهري مقسومًا على 4، والتعديل هنا يحدث الراتب الشهري تلقائيًا.',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_overtime_multiplier = fields.Float(
        string='معامل ساعة العمل الإضافي',
        default=1.0,
        required=True,
        digits=(16, 2),
        help=(
            'قيمة ساعة العمل بعد نهاية الدوام = سعر الساعة العادية × هذا '
            'المعامل. مثال: 1.25 يعني 125% من سعر الساعة العادية.'
        ),
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_allowance_line_ids = fields.One2many(
        'furniture.mrp.contract.allowance',
        'contract_id',
        string='بنود البدلات الشهرية القديمة',
        help='حقل قديم محفوظ للتوافق فقط؛ البدلات المعتمدة هي الحقول الثابتة في العقد.',
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_food_allowance = fields.Monetary(
        string='بدل أكل شهري',
        currency_field='currency_id',
        default=0.0,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_transport_allowance = fields.Monetary(
        string='بدل مواصلات شهري',
        currency_field='currency_id',
        default=0.0,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_basic_allowance = fields.Monetary(
        string='بدل أساسي شهري',
        currency_field='currency_id',
        default=0.0,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )
    furniture_total_monthly_allowances = fields.Monetary(
        string='إجمالي البدلات الشهرية',
        currency_field='currency_id',
        compute='_compute_furniture_total_monthly_allowances',
        store=True,
        groups=(
            'furniture_mrp.group_furniture_mrp_manager,'
            'hr_contract.group_hr_contract_manager'
        ),
    )

    @api.depends('wage')
    def _compute_furniture_weekly_wage(self):
        for contract in self:
            contract.furniture_weekly_wage = (contract.wage or 0.0) / 4.0

    def _inverse_furniture_weekly_wage(self):
        for contract in self:
            contract.wage = (contract.furniture_weekly_wage or 0.0) * 4.0

    @api.depends(
        'furniture_food_allowance',
        'furniture_transport_allowance',
        'furniture_basic_allowance',
    )
    def _compute_furniture_total_monthly_allowances(self):
        for contract in self:
            contract.furniture_total_monthly_allowances = (
                (contract.furniture_food_allowance or 0.0)
                + (contract.furniture_transport_allowance or 0.0)
                + (contract.furniture_basic_allowance or 0.0)
            )

    @api.constrains(
        'furniture_food_allowance',
        'furniture_transport_allowance',
        'furniture_basic_allowance',
    )
    def _check_furniture_fixed_allowances(self):
        for contract in self:
            if any(amount < 0 for amount in (
                contract.furniture_food_allowance,
                contract.furniture_transport_allowance,
                contract.furniture_basic_allowance,
            )):
                raise ValidationError(_('قيمة البدل لا يمكن أن تكون سالبة.'))

    @api.constrains('furniture_overtime_multiplier')
    def _check_furniture_overtime_multiplier(self):
        for contract in self:
            if contract.furniture_overtime_multiplier < 1.0:
                raise ValidationError(_(
                    'معامل ساعة العمل الإضافي يجب ألا يقل عن 1.00.'
                ))


class FurnitureMrpContractAllowance(models.Model):
    _name = 'furniture.mrp.contract.allowance'
    _description = 'بند بدل شهري في عقد عامل'
    _order = 'sequence, id'
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    contract_id = fields.Many2one(
        'hr.contract', required=True, ondelete='cascade', index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id', readonly=True,
    )
    name = fields.Char(string='اسم البدل', required=True)
    amount = fields.Monetary(
        string='القيمة الشهرية', currency_field='currency_id',
        required=True, default=0.0,
    )
    active = fields.Boolean(default=True)

    @api.constrains('amount')
    def _check_amount(self):
        for allowance in self:
            if allowance.amount < 0:
                raise ValidationError(_('قيمة البدل لا يمكن أن تكون سالبة.'))


class FurnitureMrpMPSAssignmentCompensation(models.Model):
    _inherit = 'furniture.mrp.mps.assignment'

    compensation_basis_snapshot = fields.Selection(
        FURNITURE_PAY_BASIS_SELECTION,
        string='طريقة الأجر عند الإكمال',
        readonly=True,
        copy=False,
        index=True,
    )
    piece_rate_snapshot = fields.Monetary(
        string='سعر القطعة عند الإكمال',
        currency_field='currency_id',
        readonly=True,
        copy=False,
    )
    piece_earning = fields.Monetary(
        string='استحقاق القطع',
        currency_field='currency_id',
        readonly=True,
        copy=False,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id',
        readonly=True,
    )
    completed_at = fields.Datetime(
        string='وقت اكتمال التكليف',
        readonly=True,
        copy=False,
        index=True,
    )

    def _freeze_furniture_compensation(self):
        to_freeze = self.filtered(
            lambda assignment: (
                assignment.state == 'done'
                and not assignment.completed_at
            )
        )
        now = fields.Datetime.now()
        for assignment in to_freeze:
            employee = assignment.employee_id.sudo()
            basis = employee.furniture_pay_basis or 'time'
            piece_rate = (
                employee.furniture_piece_rate or 0.0
                if basis == 'production'
                else 0.0
            )
            super(
                FurnitureMrpMPSAssignmentCompensation,
                assignment,
            ).write({
                'compensation_basis_snapshot': basis,
                'piece_rate_snapshot': piece_rate,
                'piece_earning': (
                    (assignment.piece_count or 0) * piece_rate
                    if basis == 'production' else 0.0
                ),
                'completed_at': now,
            })
        return True

    @api.model_create_multi
    def create(self, vals_list):
        assignments = super().create(vals_list)
        assignments._freeze_furniture_compensation()
        return assignments

    def write(self, vals):
        result = super().write(vals)
        if vals.get('state') == 'done':
            self._freeze_furniture_compensation()
        return result


class FurnitureMrpEmployeePayReport(models.TransientModel):
    _name = 'furniture.mrp.employee.pay.report'
    _description = 'كشف الأجر الأسبوعي والحضور الشهري'

    employee_id = fields.Many2one(
        'hr.employee', string='الموظف', required=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='employee_id.company_id', readonly=True,
    )
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True,
    )
    pay_basis = fields.Selection(
        FURNITURE_PAY_BASIS_SELECTION,
        string='طريقة حساب الأجر', readonly=True,
    )
    week_start = fields.Date(
        string='بداية أسبوع الأجر', required=True,
        default=lambda self: self.env['hr.employee']._furniture_week_bounds()[0],
    )
    week_end = fields.Date(
        string='نهاية أسبوع الأجر',
        compute='_compute_week_end', readonly=True,
    )
    month_start = fields.Date(
        string='بداية فترة الحضور', required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    month_end = fields.Date(
        string='نهاية فترة الحضور', required=True,
        default=fields.Date.context_today,
    )
    fixed_weekly_wage = fields.Monetary(
        string='الأجر الأسبوعي الثابت', currency_field='currency_id',
        readonly=True,
    )
    piece_rate = fields.Monetary(
        string='سعر القطعة الحالي', currency_field='currency_id',
        readonly=True,
    )
    completed_piece_qty = fields.Integer(
        string='القطع المكتملة في الأسبوع', readonly=True,
    )
    weekly_pay = fields.Monetary(
        string='أجر الأسبوع قبل الإضافي', currency_field='currency_id',
        readonly=True,
    )
    week_overtime_hours = fields.Float(
        string='ساعات الإضافي في أسبوع الأجر', readonly=True,
        digits=(16, 2),
    )
    attendance_overtime_hours = fields.Float(
        string='ساعات الإضافي في فترة الحضور', readonly=True,
        digits=(16, 2),
    )
    overtime_multiplier = fields.Float(
        string='معامل الإضافي', readonly=True, digits=(16, 2),
    )
    regular_hour_rate = fields.Monetary(
        string='سعر الساعة العادية', currency_field='currency_id',
        readonly=True,
    )
    overtime_hour_rate = fields.Monetary(
        string='سعر ساعة الإضافي', currency_field='currency_id',
        readonly=True,
    )
    overtime_amount = fields.Monetary(
        string='قيمة الإضافي في الأسبوع', currency_field='currency_id',
        readonly=True,
    )
    weekly_total_with_overtime = fields.Monetary(
        string='إجمالي الأسبوع شامل الإضافي', currency_field='currency_id',
        readonly=True,
    )
    scheduled_days = fields.Integer(string='أيام العمل المطلوبة', readonly=True)
    attendance_days = fields.Integer(string='أيام الحضور', readonly=True)
    absence_days = fields.Integer(string='أيام الغياب', readonly=True)
    late_days = fields.Integer(string='أيام التأخير', readonly=True)
    late_hours = fields.Float(
        string='إجمالي ساعات التأخير المخصومة',
        readonly=True, digits=(16, 2),
    )
    worked_hours = fields.Float(
        string='إجمالي ساعات الحضور', readonly=True, digits=(16, 2),
    )
    production_line_ids = fields.One2many(
        'furniture.mrp.employee.pay.report.production.line',
        'report_id', string='تفاصيل إنتاج الأسبوع', readonly=True,
    )
    attendance_line_ids = fields.One2many(
        'furniture.mrp.employee.pay.report.attendance.line',
        'report_id', string='تفاصيل حضور الفترة', readonly=True,
    )

    @api.depends('week_start')
    def _compute_week_end(self):
        for wizard in self:
            wizard.week_end = (
                fields.Date.to_date(wizard.week_start) + timedelta(days=6)
                if wizard.week_start else False
            )

    def _check_furniture_pay_report_access(self):
        self.employee_id._check_furniture_pay_report_access()
        return True

    def _refresh_report(self):
        self.ensure_one()
        self._check_furniture_pay_report_access()
        if not self.week_start or not self.month_start or not self.month_end:
            raise ValidationError(_('حدد فترات الأجر والحضور كاملة.'))
        if self.month_end < self.month_start:
            raise ValidationError(_('نهاية فترة الحضور تسبق بدايتها.'))

        week_end = fields.Date.to_date(self.week_start) + timedelta(days=6)
        employee = self.employee_id.sudo()
        contract = employee._furniture_contract_for_date(week_end)
        fixed_weekly_wage = (contract.wage or 0.0) / 4.0 if contract else 0.0
        piece_summary = employee._furniture_piece_pay_details(
            self.week_start, week_end,
        )
        attendance = employee._furniture_attendance_details(
            self.month_start, self.month_end,
        )
        week_attendance = employee._furniture_attendance_details(
            self.week_start, week_end,
        )
        overtime = employee._furniture_overtime_pay_details(
            self.week_start, week_end,
            contract=contract,
            attendance=week_attendance,
        )
        weekly_pay = (
            piece_summary['amount']
            if employee.furniture_pay_basis == 'production'
            else fixed_weekly_wage
        )
        production_commands = [(5, 0, 0)] + [(0, 0, {
            'assignment_id': assignment.id,
            'completed_at': assignment.completed_at,
            'stage': assignment.stage,
            'operation_name': assignment.operation_name,
            'output_summary': assignment.output_summary,
            'piece_count': assignment.piece_count,
            'piece_rate': assignment.piece_rate_snapshot,
            'amount': assignment.piece_earning,
        }) for assignment in piece_summary['assignments']]
        attendance_commands = [(5, 0, 0)] + [
            (0, 0, values) for values in attendance['lines']
        ]
        self.write({
            'pay_basis': employee.furniture_pay_basis,
            'fixed_weekly_wage': fixed_weekly_wage,
            'piece_rate': employee.furniture_piece_rate,
            'completed_piece_qty': piece_summary['piece_qty'],
            'weekly_pay': weekly_pay,
            'week_overtime_hours': overtime['hours'],
            'attendance_overtime_hours': attendance['overtime_hours'],
            'overtime_multiplier': overtime['multiplier'],
            'regular_hour_rate': overtime['regular_hour_rate'],
            'overtime_hour_rate': overtime['overtime_hour_rate'],
            'overtime_amount': overtime['amount'],
            'weekly_total_with_overtime': weekly_pay + overtime['amount'],
            'scheduled_days': attendance['scheduled_days'],
            'attendance_days': attendance['attendance_days'],
            'absence_days': attendance['absence_days'],
            'late_days': attendance['late_days'],
            'late_hours': attendance['deductible_late_hours'],
            'worked_hours': attendance['worked_hours'],
            'production_line_ids': production_commands,
            'attendance_line_ids': attendance_commands,
        })
        return self

    def _action_open(self):
        self.ensure_one()
        view = self.env.ref(
            'furniture_mrp.view_furniture_mrp_employee_pay_report_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('كشف الأجر والحضور — %s') % self.employee_id.display_name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
        }

    def action_refresh(self):
        self._refresh_report()
        return self._action_open()


class FurnitureMrpEmployeePayReportProductionLine(models.TransientModel):
    _name = 'furniture.mrp.employee.pay.report.production.line'
    _description = 'تفصيل إنتاج عامل في كشف الأجر'
    _order = 'completed_at, id'

    report_id = fields.Many2one(
        'furniture.mrp.employee.pay.report', required=True, ondelete='cascade',
    )
    currency_id = fields.Many2one(
        related='report_id.currency_id', readonly=True,
    )
    assignment_id = fields.Many2one(
        'furniture.mrp.mps.assignment', string='تكليف MPS', readonly=True,
    )
    completed_at = fields.Datetime(string='وقت الإكمال', readonly=True)
    stage = fields.Selection(
        FURNITURE_STAGE_SELECTION, string='المرحلة', readonly=True,
    )
    operation_name = fields.Char(string='العملية', readonly=True)
    output_summary = fields.Char(string='تفاصيل القطع', readonly=True)
    piece_count = fields.Integer(string='عدد القطع', readonly=True)
    piece_rate = fields.Monetary(
        string='سعر القطعة', currency_field='currency_id', readonly=True,
    )
    amount = fields.Monetary(
        string='الاستحقاق', currency_field='currency_id', readonly=True,
    )


class FurnitureMrpEmployeePayReportAttendanceLine(models.TransientModel):
    _name = 'furniture.mrp.employee.pay.report.attendance.line'
    _description = 'تفصيل حضور عامل في كشف الأجر'
    _order = 'date, id'

    report_id = fields.Many2one(
        'furniture.mrp.employee.pay.report', required=True, ondelete='cascade',
    )
    date = fields.Date(string='اليوم', required=True, readonly=True)
    status = fields.Selection(
        [('present', 'حاضر'), ('absent', 'غائب')],
        string='الحالة', required=True, readonly=True,
    )
    scheduled_hours = fields.Float(
        string='الساعات المطلوبة', readonly=True, digits=(16, 2),
    )
    worked_hours = fields.Float(
        string='ساعات الحضور', readonly=True, digits=(16, 2),
    )
    late_hours = fields.Float(
        string='التأخير الفعلي', readonly=True, digits=(16, 2),
    )
    deductible_late_hours = fields.Float(
        string='التأخير المخصوم', readonly=True, digits=(16, 2),
    )
    overtime_hours = fields.Float(
        string='ساعات إضافية', readonly=True, digits=(16, 2),
    )
