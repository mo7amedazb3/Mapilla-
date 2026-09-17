# -*- coding: utf-8 -*-
from odoo import models, fields, api


class FurnitureMrpWorkerTimeLogBase(models.AbstractModel):
    _name = 'furniture.mrp.worker.time.log.base'
    _description = 'سجل وقت العامل - أساس مشترك'
    _order = 'start_datetime desc, id desc'

    user_id = fields.Many2one(
        'res.users', string='المستخدم', required=True, ondelete='restrict', index=True,
    )
    start_datetime = fields.Datetime(
        string='بداية التوقيت', required=True, default=fields.Datetime.now, index=True,
    )
    end_datetime = fields.Datetime(string='نهاية التوقيت', index=True)
    duration_hours = fields.Float(
        string='المدة (ساعة)', compute='_compute_duration_hours', readonly=True,
        digits=(16, 2),
    )
    employee_id = fields.Many2one(
        'hr.employee', string='العامل', compute='_compute_employee_id',
        compute_sudo=True, readonly=True, store=True,
    )
    contract_id = fields.Many2one(
        'hr.contract', string='العقد', compute='_compute_contract_labor_values',
        compute_sudo=True, readonly=True,
    )
    contract_wage = fields.Float(
        string='راتب العقد', compute='_compute_contract_labor_values',
        compute_sudo=True, readonly=True, digits=(16, 2),
    )
    hourly_rate = fields.Float(
        string='تكلفة الساعة من العقد', compute='_compute_contract_labor_values',
        compute_sudo=True, readonly=True, digits=(16, 2),
    )
    labor_cost = fields.Float(
        string='تكلفة العامل', compute='_compute_contract_labor_values',
        compute_sudo=True, readonly=True, digits=(16, 2),
    )
    is_active = fields.Boolean(string='نشط', compute='_compute_is_active', readonly=True)

    @api.depends('user_id')
    def _compute_employee_id(self):
        users = self.mapped('user_id')
        employees = self.env['hr.employee'].sudo().search([('user_id', 'in', users.ids)])
        employee_by_user = {}
        for employee in employees:
            employee_by_user.setdefault(employee.user_id.id, employee)
        for rec in self:
            rec.employee_id = employee_by_user.get(rec.user_id.id)

    @api.depends('start_datetime', 'end_datetime')
    def _compute_duration_hours(self):
        now_dt = fields.Datetime.to_datetime(fields.Datetime.now())
        for rec in self:
            if not rec.start_datetime:
                rec.duration_hours = 0.0
                continue
            start_dt = fields.Datetime.to_datetime(rec.start_datetime)
            end_dt = fields.Datetime.to_datetime(rec.end_datetime) if rec.end_datetime else now_dt
            if end_dt <= start_dt:
                rec.duration_hours = 0.0
                continue
            attendance_hours = rec._compute_attendance_hours(start_dt, end_dt)
            if attendance_hours is None:
                delta = end_dt - start_dt
                rec.duration_hours = max(delta.total_seconds() / 3600.0, 0.0)
            else:
                rec.duration_hours = attendance_hours

    def _compute_attendance_hours(self, start_dt, end_dt):
        """احسب مدة التواجد الفعلية حسب hr.attendance.

        نرجع None لو موديل الحضور غير موجود أو الموظف غير مرتبط بمستخدم،
        وبخلاف ذلك نرجع إجمالي الساعات المتقاطعة فقط.
        """
        self.ensure_one()
        try:
            attendance_model = self.env['hr.attendance'].sudo()
        except KeyError:
            return None

        employee = self.env['hr.employee'].sudo().search([('user_id', '=', self.user_id.id)], limit=1)
        if not employee:
            return None

        attendances = attendance_model.search([
            ('employee_id', '=', employee.id),
            ('check_in', '<', fields.Datetime.to_string(end_dt)),
            '|',
            ('check_out', '=', False),
            ('check_out', '>', fields.Datetime.to_string(start_dt)),
        ])
        total_seconds = 0.0
        for attendance in attendances:
            check_in = fields.Datetime.to_datetime(attendance.check_in)
            check_out = fields.Datetime.to_datetime(attendance.check_out) if attendance.check_out else end_dt
            overlap_start = max(start_dt, check_in)
            overlap_end = min(end_dt, check_out)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        return max(total_seconds / 3600.0, 0.0)

    def _get_contract_for_employee(self, employee, work_date):
        if not employee:
            return self.env['hr.contract']
        Contract = self.env['hr.contract'].sudo()
        contracts = Contract.search([
            ('employee_id', '=', employee.id),
            ('state', 'in', ('open', 'close')),
        ], order='date_start desc, id desc')
        if work_date:
            for contract in contracts:
                starts_before = not contract.date_start or contract.date_start <= work_date
                ends_after = not contract.date_end or contract.date_end >= work_date
                if starts_before and ends_after:
                    return contract
        current_contract = employee.sudo().contract_id
        if current_contract and current_contract.employee_id == employee:
            return current_contract.sudo()
        return Contract.search([('employee_id', '=', employee.id)], order='date_start desc, id desc', limit=1)

    def _get_calendar_weekly_hours(self, calendar):
        if not calendar:
            return 0.0
        attendances = calendar.sudo().attendance_ids.filtered(lambda line: not line.display_type)
        if not attendances:
            return (calendar.hours_per_day or 0.0) * 5.0
        if calendar.two_weeks_calendar and 'week_type' in attendances._fields:
            week_totals = {}
            for attendance in attendances:
                week_key = attendance.week_type or '0'
                week_totals[week_key] = week_totals.get(week_key, 0.0) + max(
                    (attendance.hour_to or 0.0) - (attendance.hour_from or 0.0),
                    0.0,
                )
            totals = [total for total in week_totals.values() if total]
            return sum(totals) / len(totals) if totals else 0.0
        return sum(
            max((attendance.hour_to or 0.0) - (attendance.hour_from or 0.0), 0.0)
            for attendance in attendances
        )

    def _get_contract_monthly_hours(self, contract, employee):
        calendar = (
            contract.resource_calendar_id
            or employee.resource_calendar_id
            or employee.company_id.resource_calendar_id
            or self.env.company.resource_calendar_id
        )
        weekly_hours = self._get_calendar_weekly_hours(calendar)
        if not weekly_hours:
            return 0.0
        if calendar and calendar.hours_per_day:
            return (calendar.hours_per_day or 0.0) * 30.0
        attendances = (
            calendar.sudo().attendance_ids.filtered(lambda line: not line.display_type)
            if calendar else self.env['resource.calendar.attendance']
        )
        working_days = len(set(attendances.mapped('dayofweek')))
        return (weekly_hours / working_days * 30.0) if working_days else (weekly_hours / 7.0 * 30.0)

    @api.depends('employee_id', 'start_datetime', 'end_datetime', 'duration_hours')
    def _compute_contract_labor_values(self):
        for rec in self:
            employee = rec.employee_id.sudo()
            work_date = fields.Date.to_date(rec.start_datetime or rec.end_datetime or fields.Datetime.now())
            contract = rec._get_contract_for_employee(employee, work_date) if employee else self.env['hr.contract']
            wage = contract.wage or 0.0
            monthly_hours = rec._get_contract_monthly_hours(contract, employee) if contract and employee else 0.0
            hourly_rate = wage / monthly_hours if wage and monthly_hours else 0.0
            rec.contract_id = contract
            rec.contract_wage = wage
            rec.hourly_rate = hourly_rate
            rec.labor_cost = (rec.duration_hours or 0.0) * hourly_rate

    @api.depends('end_datetime')
    def _compute_is_active(self):
        for rec in self:
            rec.is_active = not bool(rec.end_datetime)


class FurnitureMrpPrimingWorkerLog(models.Model):
    _name = 'furniture.mrp.priming.worker.log'
    _description = 'سجل وقت عمال التقديم'
    _inherit = 'furniture.mrp.worker.time.log.base'

    priming_id = fields.Many2one(
        'furniture.mrp.priming', string='أمر التقديم',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpPaintingWorkerLog(models.Model):
    _name = 'furniture.mrp.painting.worker.log'
    _description = 'سجل وقت عمال تصنيع الدهانات'
    _inherit = 'furniture.mrp.worker.time.log.base'

    painting_id = fields.Many2one(
        'furniture.mrp.painting', string='أمر تصنيع الدهانات',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpCarpentryWorkerLog(models.Model):
    _name = 'furniture.mrp.carpentry.worker.log'
    _description = 'سجل وقت عمال التجميع'
    _inherit = 'furniture.mrp.worker.time.log.base'

    carpentry_id = fields.Many2one(
        'furniture.mrp.carpentry', string='أمر التجميع',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpFinishingWorkerLog(models.Model):
    _name = 'furniture.mrp.finishing.worker.log'
    _description = 'سجل وقت عمال التجهيز'
    _inherit = 'furniture.mrp.worker.time.log.base'

    finishing_id = fields.Many2one(
        'furniture.mrp.finishing', string='أمر التجهيز',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpBasesWorkerLog(models.Model):
    _name = 'furniture.mrp.bases.worker.log'
    _description = 'سجل وقت عمال القواعد'
    _inherit = 'furniture.mrp.worker.time.log.base'

    bases_id = fields.Many2one(
        'furniture.mrp.bases', string='أمر القواعد',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpTailoringWorkerLog(models.Model):
    _name = 'furniture.mrp.tailoring.worker.log'
    _description = 'سجل وقت عمال التفصيل'
    _inherit = 'furniture.mrp.worker.time.log.base'

    tailoring_id = fields.Many2one(
        'furniture.mrp.tailoring', string='أمر التفصيل',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpUpholsteryWorkerLog(models.Model):
    _name = 'furniture.mrp.upholstery.worker.log'
    _description = 'سجل وقت عمال الكسوة'
    _inherit = 'furniture.mrp.worker.time.log.base'

    upholstery_id = fields.Many2one(
        'furniture.mrp.upholstery', string='أمر الكسوة',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpSewingWorkerLog(models.Model):
    _name = 'furniture.mrp.sewing.worker.log'
    _description = 'سجل وقت عمال الخياطة'
    _inherit = 'furniture.mrp.worker.time.log.base'

    sewing_id = fields.Many2one(
        'furniture.mrp.sewing', string='أمر الخياطة',
        required=True, ondelete='cascade', index=True,
    )


class FurnitureMrpPackagingWorkerLog(models.Model):
    _name = 'furniture.mrp.packaging.worker.log'
    _description = 'سجل وقت عمال التغليف'
    _inherit = 'furniture.mrp.worker.time.log.base'

    packaging_id = fields.Many2one(
        'furniture.mrp.packaging', string='أمر التغليف',
        required=True, ondelete='cascade', index=True,
    )
