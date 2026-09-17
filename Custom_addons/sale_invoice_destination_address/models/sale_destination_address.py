# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleDestinationAddress(models.Model):
    _name = 'sale.destination.address'
    _description = 'Sales Destination Address'
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(
        string='Address',
        required=True,
        index=True,
        help='City or destination used on quotations and customer invoices.',
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete='cascade',
    )

    _sql_constraints = [
        (
            'name_company_unique',
            'unique(name, company_id)',
            'An address with this name already exists for this company.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        cleaned_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if 'name' in vals:
                vals['name'] = (vals.get('name') or '').strip()
            cleaned_vals_list.append(vals)
        return super().create(cleaned_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'name' in vals:
            vals['name'] = (vals.get('name') or '').strip()
        return super().write(vals)

    @api.constrains('name', 'company_id')
    def _check_name(self):
        for address in self:
            if not address.name:
                raise ValidationError(_('The address name cannot be empty.'))
            duplicate = self.with_context(active_test=False).search_count([
                ('id', '!=', address.id),
                ('company_id', '=', address.company_id.id),
                ('name', '=ilike', address.name),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    'An address with the name “%(name)s” already exists.',
                    name=address.name,
                ))
