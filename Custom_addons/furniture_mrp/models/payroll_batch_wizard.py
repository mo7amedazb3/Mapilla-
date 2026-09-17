# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError


class FurnitureMrpPayrollBatchWizard(models.TransientModel):
    _name = 'furniture.mrp.payroll.batch.wizard'
    _description = 'Create Furniture Payroll Slips for All Workers'

    company_id = fields.Many2one(
        'res.company', string='الشركة', required=True,
        default=lambda self: self.env.company, readonly=True,
    )
    date_from = fields.Date(
        string='من تاريخ', required=True,
        default=lambda self: fields.Date.context_today(self) - timedelta(days=6),
    )
    date_to = fields.Date(
        string='إلى تاريخ', required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    line_ids = fields.One2many(
        'furniture.mrp.payroll.batch.wizard.line', 'wizard_id',
        string='العمال', default=lambda self: self._default_line_commands(),
    )
    slip_ids = fields.Many2many(
        'simple.payroll.slip',
        'furniture_mrp_payroll_batch_wizard_slip_rel',
        'wizard_id', 'slip_id', string='كشوف الأجر الناتجة', readonly=True,
    )
    employee_count = fields.Integer(
        string='إجمالي العمال', compute='_compute_counts',
    )
    selected_count = fields.Integer(
        string='المحددون', compute='_compute_counts',
    )
    missing_contract_count = fields.Integer(
        string='بدون عقد صالح', compute='_compute_counts',
    )
    zero_wage_count = fields.Integer(
        string='أجرهم صفر', compute='_compute_counts',
    )
    slip_count = fields.Integer(
        string='الكشوف الناتجة', compute='_compute_counts',
    )

    @api.model
    def _default_line_commands(self):
        employees = self.env['hr.employee'].search([
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
        ], order='name, id')
        return [
            Command.create({
                'sequence': sequence,
                'selected': True,
                'employee_id': employee.id,
            })
            for sequence, employee in enumerate(employees, start=1)
        ]

    @api.depends(
        'line_ids', 'line_ids.selected', 'line_ids.contract_id',
        'line_ids.contract_id.wage', 'slip_ids',
    )
    def _compute_counts(self):
        for wizard in self:
            selected_lines = wizard.line_ids.filtered('selected')
            wizard.employee_count = len(wizard.line_ids)
            wizard.selected_count = len(selected_lines)
            wizard.missing_contract_count = len(
                selected_lines.filtered(lambda line: not line.contract_id)
            )
            wizard.zero_wage_count = len(selected_lines.filtered(
                lambda line: line.contract_id and not line.contract_id.wage
            ))
            wizard.slip_count = len(wizard.slip_ids)

    def _selected_employees(self):
        self.ensure_one()
        return self.line_ids.filtered('selected').mapped('employee_id').sorted(
            key=lambda employee: (employee.name or '', employee.id),
        )

    def _preflight(self):
        self.ensure_one()
        helper = self.env['furniture.mrp.payroll.slip.wizard']
        helper._validate_period(self.company_id, self.date_from, self.date_to)
        employees = self._selected_employees()
        if not employees:
            raise ValidationError(_('حدد عاملًا واحدًا على الأقل لإنشاء الكشوف.'))

        for employee in employees:
            helper._validate_employee(employee, self.company_id)

        # Serialise generation per employee.  A second concurrent batch waits,
        # then sees and reuses the slips created by the first transaction.
        helper._lock_employees(employees)

        resolutions = {}
        overlap_messages = []
        missing_contract_names = []
        for employee in employees:
            exact, other_overlap = helper._resolve_employee_period(
                employee, self.company_id, self.date_from, self.date_to,
            )
            resolutions[employee.id] = exact
            if other_overlap:
                periods = '، '.join(
                    '%s — %s' % (
                        fields.Date.to_string(slip.date_from),
                        fields.Date.to_string(slip.date_to),
                    )
                    for slip in other_overlap
                )
                overlap_messages.append('%s (%s)' % (
                    employee.display_name, periods,
                ))
            if (
                (not exact or exact.state == 'draft')
                and not helper._contract_for_period(
                    employee, self.company_id, self.date_from, self.date_to,
                )
            ):
                missing_contract_names.append(employee.display_name)

        errors = []
        if overlap_messages:
            errors.append(
                _('فترات متداخلة:\n- %s') % '\n- '.join(overlap_messages)
            )
        if missing_contract_names:
            errors.append(
                _('لا يوجد عقد صالح خلال الفترة:\n- %s')
                % '\n- '.join(missing_contract_names)
            )
        if errors:
            raise UserError('\n\n'.join(errors))
        return employees, resolutions

    def _generate_slips(self):
        self.ensure_one()
        employees, resolutions = self._preflight()
        Slip = self.env['simple.payroll.slip']
        slips = Slip.browse()
        for employee in employees:
            slip = resolutions[employee.id]
            if not slip:
                slip = Slip.create({
                    'employee_id': employee.id,
                    'company_id': self.company_id.id,
                    'date_from': self.date_from,
                    'date_to': self.date_to,
                })
            if slip.state == 'draft':
                slip.action_recompute_factory_payroll()
            slips |= slip

        self.slip_ids = [Command.set(slips.ids)]
        return slips.sorted(
            key=lambda slip: (slip.employee_id.name or '', slip.employee_id.id),
        )

    def action_generate_and_open(self):
        slips = self._generate_slips()
        pay_basis = self.env.context.get('furniture_payroll_pay_basis')
        if pay_basis not in ('time', 'production'):
            pay_basis = False
        action_domain = [('id', 'in', slips.ids)]
        action_context = {
            'create': False,
            'default_company_id': self.company_id.id,
            'search_default_group_employee': False,
            'furniture_payroll_date_from': fields.Date.to_string(
                self.date_from
            ),
            'furniture_payroll_date_to': fields.Date.to_string(
                self.date_to
            ),
        }
        action_name = _('كشوف أجور كل العمال')
        if pay_basis:
            action_domain.append(('furniture_pay_basis', '=', pay_basis))
            action_context['furniture_payroll_pay_basis'] = pay_basis
            action_name = (
                _('كشوف أجور عمال الإنتاج')
                if pay_basis == 'production'
                else _('كشوف أجور عمال الوقت')
            )
        return {
            'type': 'ir.actions.act_window',
            'name': action_name,
            'res_model': 'simple.payroll.slip',
            'view_mode': 'list,form',
            'views': [
                (
                    self.env.ref(
                        'simple_payroll_salary-2.view_simple_payroll_slip_list'
                    ).id,
                    'list',
                ),
                (
                    self.env.ref(
                        'simple_payroll_salary-2.view_simple_payroll_slip_form'
                    ).id,
                    'form',
                ),
            ],
            'domain': action_domain,
            'context': action_context,
            'target': 'current',
        }

    @api.model
    def _period_wizard(self, date_from, date_to):
        date_from = fields.Date.to_date(date_from) if date_from else False
        date_to = fields.Date.to_date(date_to) if date_to else False
        helper = self.env['furniture.mrp.payroll.slip.wizard']
        helper._validate_period(
            self.env.company, date_from, date_to,
        )
        wizard = self.create({
            'company_id': self.env.company.id,
            'date_from': date_from,
            'date_to': date_to,
        })
        # The direct list action has no selection screen.  Infrastructure or
        # placeholder employees without a valid contract must not prevent all
        # contracted workers from loading or posting their weekly payroll.
        for line in wizard.line_ids:
            if not helper._contract_for_period(
                line.employee_id, wizard.company_id, date_from, date_to,
            ):
                line.selected = False
        return wizard

    @api.model
    def action_generate_period_slips(self, date_from, date_to):
        """Generate/reuse contracted workers' slips for a list-selected period."""
        wizard = self._period_wizard(date_from, date_to)
        return wizard.action_generate_and_open()

    def action_generate_and_print(self):
        self._generate_slips()
        return self.env.ref(
            'furniture_mrp.action_report_furniture_payroll_batch'
        ).report_action(self, config=False)


class FurnitureMrpPayrollBatchWizardLine(models.TransientModel):
    _name = 'furniture.mrp.payroll.batch.wizard.line'
    _description = 'Furniture Payroll Batch Worker'
    _order = 'sequence, employee_id, id'

    wizard_id = fields.Many2one(
        'furniture.mrp.payroll.batch.wizard', required=True,
        ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    selected = fields.Boolean(string='إضافة', default=True)
    employee_id = fields.Many2one(
        'hr.employee', string='العامل', required=True, readonly=True,
    )
    department_id = fields.Many2one(
        'hr.department', string='القسم',
        related='employee_id.department_id', readonly=True,
    )
    pay_basis = fields.Selection(
        related='employee_id.furniture_pay_basis',
        string='طريقة حساب الأجر', readonly=True,
    )
    contract_id = fields.Many2one(
        'hr.contract', string='العقد خلال الفترة',
        compute='_compute_contract_id', readonly=True,
    )

    @api.depends(
        'employee_id', 'wizard_id.company_id',
        'wizard_id.date_from', 'wizard_id.date_to',
    )
    def _compute_contract_id(self):
        helper = self.env['furniture.mrp.payroll.slip.wizard']
        for line in self:
            wizard = line.wizard_id
            if (
                not line.employee_id
                or not wizard.company_id
                or not wizard.date_from
                or not wizard.date_to
            ):
                line.contract_id = False
                continue
            line.contract_id = helper._contract_for_period(
                line.employee_id,
                wizard.company_id,
                wizard.date_from,
                wizard.date_to,
            )
