from odoo import api, fields, models, _

class HrContract(models.Model):
    _inherit = 'hr.contract'

    # Use company's currency for allowances; works even if hr_contract lacks its own currency field
    allowance_currency_id = fields.Many2one(
        'res.currency',
        related='company_id.currency_id',
        store=True,
        readonly=True
    )

    food_allowance = fields.Monetary(
        string='Food Allowance',
        currency_field='allowance_currency_id',
        default=0.0
    )
    transfer_allowance = fields.Monetary(
        string='Transfer Allowance',
        currency_field='allowance_currency_id',
        default=0.0
    )
    commission_allowance = fields.Monetary(
        string='Commission',
        currency_field='allowance_currency_id',
        default=0.0
    )
    other_allowance = fields.Monetary(
        string='Other Allowance',
        currency_field='allowance_currency_id',
        default=0.0
    )

    total_allowances = fields.Monetary(
        string='Total Allowances',
        currency_field='allowance_currency_id',
        compute='_compute_total_allowances',
        store=True,
        readonly=True
    )
    total_compensation = fields.Monetary(
        string='Wage + Allowances',
        currency_field='allowance_currency_id',
        compute='_compute_total_compensation',
        store=True,
        readonly=True
    )

    @api.depends('food_allowance', 'transfer_allowance', 'commission_allowance', 'other_allowance')
    def _compute_total_allowances(self):
        for rec in self:
            rec.total_allowances = (rec.food_allowance or 0.0) + (rec.transfer_allowance or 0.0) + (rec.commission_allowance or 0.0) + (rec.other_allowance or 0.0)

    @api.depends('wage', 'total_allowances')
    def _compute_total_compensation(self):
        for rec in self:
            rec.total_compensation = (rec.wage or 0.0) + (rec.total_allowances or 0.0)