# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class FurnitureMrpPayrollSlipWizard(models.TransientModel):
    _name = 'furniture.mrp.payroll.slip.wizard'
    _description = 'Create One Furniture Payroll Slip'

    company_id = fields.Many2one(
        'res.company',
        string='الشركة',
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='العامل',
        required=True,
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
    )
    date_from = fields.Date(
        string='من تاريخ',
        required=True,
        default=lambda self: self._default_date_from(),
    )
    date_to = fields.Date(
        string='إلى تاريخ',
        required=True,
        default=lambda self: fields.Date.context_today(self),
    )

    @api.model
    def _default_date_from(self):
        return self._rolling_period_start(fields.Date.context_today(self))

    @api.model
    def _rolling_period_start(self, date_to):
        return date_to - timedelta(days=6)

    def _check_payroll_manager_access(self):
        allowed_groups = (
            'furniture_mrp.group_furniture_mrp_manager',
            'hr.group_hr_manager',
            'hr_contract.group_hr_contract_manager',
            'hr_attendance.group_hr_attendance_manager',
        )
        if not any(self.env.user.has_group(group) for group in allowed_groups):
            raise AccessError(
                _('إنشاء وطباعة كشوف الأجر متاح لمديري الرواتب فقط.')
            )

    @api.model
    def _validate_period(self, company, date_from, date_to):
        self._check_payroll_manager_access()
        if not date_from or not date_to:
            raise ValidationError(_('حدد تاريخ بداية ونهاية فترة كشف الأجر.'))
        if date_from > date_to:
            raise ValidationError(
                _('تاريخ البداية يجب أن يسبق تاريخ النهاية.')
            )
        today = fields.Date.context_today(self)
        if date_to > today and not self._is_current_thursday_payroll_week(
            date_from, date_to, today,
        ):
            raise ValidationError(
                _('لا يمكن إنشاء كشف أجر عن فترة تنتهي بعد تاريخ اليوم.')
            )
        if company not in self.env.companies:
            raise AccessError(_('لا يمكنك إنشاء كشف لشركة غير مسموحة لك.'))

    @api.model
    def _is_current_thursday_payroll_week(self, date_from, date_to, today):
        """Allow closing Sat-Fri on Thursday because Friday is the weekly day off."""
        return bool(
            today
            and today.weekday() == 3
            and date_from == today - timedelta(days=5)
            and date_to == today + timedelta(days=1)
        )

    @api.model
    def _validate_employee(self, employee, company):
        if not employee.active or employee.company_id != company:
            raise ValidationError(
                _('اختر عاملًا نشطًا تابعًا للشركة المحددة.')
            )

    @api.model
    def _contract_for_period(self, employee, company, date_from, date_to):
        return self.env['hr.contract'].sudo().search([
            ('employee_id', '=', employee.id),
            ('company_id', '=', company.id),
            ('state', 'in', ['open', 'close']),
            ('date_start', '<=', date_to),
            '|',
            ('date_end', '=', False),
            ('date_end', '>=', date_from),
        ], order='date_start desc, id desc', limit=1)

    @api.model
    def _resolve_employee_period(self, employee, company, date_from, date_to):
        overlapping = self.env['simple.payroll.slip'].search([
            ('employee_id', '=', employee.id),
            ('company_id', '=', company.id),
            ('state', '!=', 'cancel'),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ], order='date_from desc, id desc')
        exact = overlapping.filtered(
            lambda slip: (
                slip.date_from == date_from and slip.date_to == date_to
            )
        )[:1]
        return exact, overlapping - exact

    @api.model
    def _lock_employees(self, employees):
        employee_ids = sorted(set(employees.ids))
        if employee_ids:
            self.env.cr.execute(
                'SELECT id FROM hr_employee WHERE id IN %s '
                'ORDER BY id FOR UPDATE',
                [tuple(employee_ids)],
            )

    @api.model
    def _get_or_create_employee_slip(
        self, employee, company, date_from, date_to, lock=True,
    ):
        self._validate_period(company, date_from, date_to)
        self._validate_employee(employee, company)
        if lock:
            self._lock_employees(employee)

        exact, other_overlap = self._resolve_employee_period(
            employee, company, date_from, date_to,
        )
        if other_overlap:
            existing = other_overlap[0]
            raise UserError(_(
                'يوجد كشف أجر متداخل للعامل %(employee)s عن الفترة '
                '%(date_from)s إلى %(date_to)s. افتح الكشف الموجود أو اختر '
                'فترة غير متداخلة.',
                employee=employee.display_name,
                date_from=fields.Date.to_string(existing.date_from),
                date_to=fields.Date.to_string(existing.date_to),
            ))

        # Validate the contract before creating a persistent slip.  This also
        # lets the all-workers wizard preflight every worker atomically.
        if (not exact or exact.state == 'draft') and not self._contract_for_period(
            employee, company, date_from, date_to,
        ):
            raise UserError(_(
                'لا يوجد عقد صالح للعامل %(employee)s يغطي الفترة المحددة.',
                employee=employee.display_name,
            ))

        slip = exact
        if not slip:
            slip = self.env['simple.payroll.slip'].create({
                'employee_id': employee.id,
                'company_id': company.id,
                'date_from': date_from,
                'date_to': date_to,
            })

        if slip.state == 'draft':
            slip.action_recompute_factory_payroll()
            if not slip.contract_id:
                raise UserError(_(
                    'لا يوجد عقد صالح للعامل %(employee)s يغطي الفترة '
                    'المحددة.',
                    employee=employee.display_name,
                ))
        return slip

    def _validate_selection(self):
        self.ensure_one()
        self._validate_period(self.company_id, self.date_from, self.date_to)
        self._validate_employee(self.employee_id, self.company_id)

    def _get_or_create_slip(self):
        self.ensure_one()
        self._validate_selection()
        return self._get_or_create_employee_slip(
            self.employee_id,
            self.company_id,
            self.date_from,
            self.date_to,
        )

    def action_open_slip(self):
        slip = self._get_or_create_slip()
        return {
            'type': 'ir.actions.act_window',
            'name': _('كشف أجر العامل'),
            'res_model': 'simple.payroll.slip',
            'res_id': slip.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref(
                    'simple_payroll_salary-2.view_simple_payroll_slip_form'
                ).id,
                'form',
            )],
            'target': 'current',
        }

    def action_print_pdf(self):
        slip = self._get_or_create_slip()
        return self.env.ref(
            'simple_payroll_salary-2.action_report_simple_payroll_slip'
        ).report_action(slip, config=False)
