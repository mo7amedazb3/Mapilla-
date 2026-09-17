# -*- coding: utf-8 -*-

from collections import defaultdict
from dateutil.relativedelta import relativedelta
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class SpaCostingPeriod(models.Model):
    _name = 'spa.costing.period'
    _description = 'SPA Monthly Costing'
    _order = 'period_date desc, id desc'

    name = fields.Char(compute='_compute_dates', store=True)
    period_date = fields.Date(
        string="Month",
        required=True,
        default=lambda self: fields.Date.today().replace(day=1),
        index=True,
    )
    date_from = fields.Date(compute='_compute_dates', store=True)
    date_to = fields.Date(compute='_compute_dates', store=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='company_id.currency_id',
        readonly=True,
    )
    conversion_cost = fields.Monetary(
        string="Monthly Conversion Cost",
        currency_field='currency_id',
        required=True,
        default=0.0,
        help="Conversion cost calculated externally by the accountant and entered at the start of the month.",
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('calculated', 'Calculated'),
            ('approved', 'Approved'),
        ],
        required=True,
        default='draft',
        index=True,
    )
    department_line_ids = fields.One2many(
        'spa.costing.department.cost',
        'period_id',
        string="Department Allocation",
        copy=False,
    )
    result_line_ids = fields.One2many(
        'spa.costing.result',
        'period_id',
        string="Costing Results",
        copy=False,
    )
    revenue_total = fields.Monetary(
        currency_field='currency_id',
        compute='_compute_result_totals',
    )
    material_cost_total = fields.Monetary(
        currency_field='currency_id',
        compute='_compute_result_totals',
    )
    allocated_conversion_total = fields.Monetary(
        currency_field='currency_id',
        compute='_compute_result_totals',
    )
    profit_total = fields.Monetary(
        currency_field='currency_id',
        compute='_compute_result_totals',
    )

    _sql_constraints = [
        (
            'period_company_uniq',
            'unique(period_date, company_id)',
            'Only one costing period is allowed per company and month.',
        ),
        (
            'conversion_cost_non_negative',
            'check(conversion_cost >= 0)',
            'Monthly conversion cost cannot be negative.',
        ),
    ]

    @api.depends('period_date')
    def _compute_dates(self):
        for period in self:
            if not period.period_date:
                period.name = False
                period.date_from = False
                period.date_to = False
                continue
            month_start = period.period_date.replace(day=1)
            period.name = month_start.strftime('%B %Y')
            period.date_from = month_start
            period.date_to = month_start + relativedelta(months=1, days=-1)

    @api.depends(
        'result_line_ids.sale_amount',
        'result_line_ids.material_cost',
        'result_line_ids.conversion_cost',
        'result_line_ids.profit_amount',
    )
    def _compute_result_totals(self):
        for period in self:
            period.revenue_total = sum(period.result_line_ids.mapped('sale_amount'))
            period.material_cost_total = sum(period.result_line_ids.mapped('material_cost'))
            period.allocated_conversion_total = sum(period.result_line_ids.mapped('conversion_cost'))
            period.profit_total = sum(period.result_line_ids.mapped('profit_amount'))

    @api.constrains('period_date')
    def _check_period_date(self):
        for period in self:
            if period.period_date and period.period_date.day != 1:
                raise ValidationError(_("The period date must be the first day of the month."))

    @api.onchange('period_date')
    def _onchange_period_date(self):
        if self.period_date:
            self.period_date = self.period_date.replace(day=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('period_date'):
                period_date = fields.Date.to_date(vals['period_date'])
                vals['period_date'] = period_date.replace(day=1)
        periods = super().create(vals_list)
        for period in periods.filtered(lambda record: record.conversion_cost):
            period.with_context(skip_auto_calculate=True).action_calculate()
        return periods

    def write(self, vals):
        if 'period_date' in vals and vals['period_date']:
            period_date = fields.Date.to_date(vals['period_date'])
            vals['period_date'] = period_date.replace(day=1)
        protected_fields = {
            'period_date',
            'company_id',
            'conversion_cost',
            'department_line_ids',
            'result_line_ids',
        }
        for period in self:
            if period.state == 'approved' and protected_fields.intersection(vals):
                raise UserError(_("An approved costing period is locked. Reset it to draft first."))
        result = super().write(vals)
        if (
            'conversion_cost' in vals
            and not self.env.context.get('skip_auto_calculate')
        ):
            self.filtered(lambda period: period.state != 'approved').with_context(
                skip_auto_calculate=True
            ).action_calculate()
        return result

    def unlink(self):
        if any(period.state == 'approved' for period in self):
            raise UserError(_("Approved costing periods cannot be deleted."))
        return super().unlink()

    def _invoice_line_domain(self, date_from, date_to, include_refunds=True):
        move_types = ['out_invoice', 'out_refund'] if include_refunds else ['out_invoice']
        return [
            ('move_id.state', '=', 'posted'),
            ('move_id.move_type', 'in', move_types),
            ('move_id.invoice_date', '>=', date_from),
            ('move_id.invoice_date', '<=', date_to),
            ('move_id.company_id', '=', self.company_id.id),
            ('display_type', '=', 'product'),
            ('product_id.product_tmpl_id.costing_eligible', '=', True),
        ]


    def _get_department(self, invoice_line):
        department = invoice_line.product_id.product_tmpl_id.costing_department_id
        if not department:
            raise UserError(
                _("Service '%s' does not have a costing department.")
                % invoice_line.product_id.display_name
            )
        if department.company_id != self.company_id:
            raise UserError(
                _("Service '%s' belongs to a department in another company.")
                % invoice_line.product_id.display_name
            )
        return department

    def _get_prior_activity(self):
        self.ensure_one()
        prior_from = self.date_from - relativedelta(months=1)
        prior_to = self.date_from - relativedelta(days=1)
        lines = self.env['account.move.line'].search(
            self._invoice_line_domain(prior_from, prior_to, include_refunds=False)
        )
        activity = defaultdict(float)
        for line in lines:
            department = self._get_department(line)
            activity[department.id] += abs(line.quantity)
        return activity

    def _get_current_activity(self, invoice_lines):
        self.ensure_one()
        activity = defaultdict(float)
        for line in invoice_lines.filtered(
            lambda invoice_line: invoice_line.move_id.move_type == 'out_invoice'
        ):
            department = self._get_department(line)
            activity[department.id] += abs(line.quantity)
        return activity

    def _prepare_department_lines(
        self,
        prior_orders,
        current_orders,
        current_departments,
    ):
        self.ensure_one()
        departments = self.env['spa.service.department'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ])
        departments |= current_departments
        total_previous_orders = sum(
            max(prior_orders[department.id], 0.0)
            for department in departments
        )
        if self.conversion_cost and not total_previous_orders:
            raise UserError(_(
                "There are no service orders in the previous month. "
                "The monthly conversion cost cannot be distributed by department."
            ))

        commands = [(5, 0, 0)]
        rates = {}
        for department in departments:
            department_orders = max(prior_orders[department.id], 0.0)
            share = (
                department_orders / total_previous_orders
                if total_previous_orders
                else 0.0
            )
            allocated = self.conversion_cost * share
            department_current_orders = max(current_orders[department.id], 0.0)
            rate = (
                allocated / department_current_orders
                if department_current_orders
                else 0.0
            )
            rates[department.id] = rate
            commands.append((0, 0, {
                'department_id': department.id,
                'previous_orders': department_orders,
                'current_orders': department_current_orders,
                'allocation_weight': share * 100.0,
                'conversion_cost': allocated,
                'cost_rate': rate,
            }))
        self.department_line_ids = commands
        return rates

    def action_calculate(self):
        for period in self:
            if period.state == 'approved':
                raise UserError(_("Reset the approved period to draft before recalculating it."))

            invoice_lines = self.env['account.move.line'].search(
                period._invoice_line_domain(period.date_from, period.date_to)
            )
            current_departments = self.env['spa.service.department']
            for line in invoice_lines:
                current_departments |= period._get_department(line)

            prior_orders = period._get_prior_activity()
            current_orders = period._get_current_activity(invoice_lines)
            rates = period._prepare_department_lines(
                prior_orders,
                current_orders,
                current_departments,
            )
            result_commands = [(5, 0, 0)]

            for line in invoice_lines:
                product = line.product_id.product_tmpl_id
                department = period._get_department(line)
                sign = -1.0 if line.move_id.move_type == 'out_refund' else 1.0
                quantity = abs(line.quantity)
                sale_abs = abs(line.price_subtotal)
                material_abs = (
                    line.spa_material_cost
                    if line.spa_material_consumed
                    else product.costing_material_cost * quantity
                )

                conversion_abs = rates.get(department.id, 0.0) * quantity
                profit_abs = sale_abs - material_abs - conversion_abs

                result_commands.append((0, 0, {
                    'move_line_id': line.id,
                    'invoice_date': line.move_id.invoice_date,
                    'product_id': line.product_id.id,
                    'department_id': department.id,
                    'quantity': sign * quantity,
                    'sale_amount': sign * sale_abs,
                    'material_cost': sign * material_abs,
                    'conversion_cost': sign * conversion_abs,
                    'profit_amount': sign * profit_abs,
                }))

            period.result_line_ids = result_commands
            period.state = 'calculated'
        return True

    def action_approve(self):
        for period in self:
            if period.state != 'calculated':
                raise UserError(_("Calculate the period before approving it."))
            period.state = 'approved'
        return True

    def action_reset_draft(self):
        self.write({'state': 'draft'})
        return True


class SpaCostingDepartmentCost(models.Model):
    _name = 'spa.costing.department.cost'
    _description = 'SPA Department Cost Allocation'
    _order = 'department_id'

    period_id = fields.Many2one(
        'spa.costing.period',
        required=True,
        ondelete='cascade',
        index=True,
    )
    department_id = fields.Many2one(
        'spa.service.department',
        required=True,
        ondelete='restrict',
    )
    currency_id = fields.Many2one(related='period_id.currency_id', readonly=True)
    previous_orders = fields.Float(string="Previous Month Orders", readonly=True)
    current_orders = fields.Float(string="Current Month Orders", readonly=True)
    allocation_weight = fields.Float(string="Allocation (%)", readonly=True)
    allocation_percentage_display = fields.Char(
        string="Allocation (%)",
        compute="_compute_allocation_percentage_display",
    )
    conversion_cost = fields.Monetary(
        string="Allocated Department Cost",
        currency_field='currency_id',
        readonly=True,
    )
    cost_rate = fields.Float(
        string="Conversion Cost / Order",
        digits=(16, 6),
        readonly=True,
    )

    @api.depends('allocation_weight')
    def _compute_allocation_percentage_display(self):
        for line in self:
            line.allocation_percentage_display = f"{line.allocation_weight:.2f}%"


class SpaCostingResult(models.Model):
    _name = 'spa.costing.result'
    _description = 'SPA Costing Result'
    _order = 'invoice_date desc, id desc'

    period_id = fields.Many2one(
        'spa.costing.period',
        required=True,
        ondelete='cascade',
        index=True,
    )
    move_line_id = fields.Many2one(
        'account.move.line',
        string="Invoice Line",
        required=True,
        ondelete='restrict',
        index=True,
    )
    invoice_id = fields.Many2one(
        'account.move',
        related='move_line_id.move_id',
        string="Invoice",
        store=True,
        readonly=True,
    )
    invoice_date = fields.Date(required=True, index=True)
    product_id = fields.Many2one('product.product', string="Service", required=True, ondelete='restrict')
    department_id = fields.Many2one('spa.service.department', required=True, ondelete='restrict')
    currency_id = fields.Many2one(related='period_id.currency_id', readonly=True)
    quantity = fields.Float(readonly=True)
    sale_amount = fields.Monetary(currency_field='currency_id', readonly=True)
    material_cost = fields.Monetary(currency_field='currency_id', readonly=True)
    conversion_cost = fields.Monetary(currency_field='currency_id', readonly=True)
    profit_amount = fields.Monetary(currency_field='currency_id', readonly=True)

    _sql_constraints = [
        (
            'period_move_line_uniq',
            'unique(period_id, move_line_id)',
            'An invoice line can only be calculated once in a costing period.',
        ),
    ]
