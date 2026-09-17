# -*- coding: utf-8 -*-

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    destination_address = fields.Char(
        string='Legacy Destination Address',
        copy=True,
        help='المدينة أو العنوان الذي ستتجه إليه منتجات هذه الفاتورة.',
    )
    destination_address_id = fields.Many2one(
        'sale.destination.address',
        string='العنوان',
        copy=True,
        check_company=True,
        ondelete='restrict',
        help='اختر المدينة أو وجهة تسليم منتجات هذه الفاتورة.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        address_model = self.env['sale.destination.address']
        prepared_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if 'destination_address_id' in vals and 'destination_address' not in vals:
                address = address_model.browse(vals.get('destination_address_id')).exists()
                vals['destination_address'] = address.name if address else False
            prepared_vals_list.append(vals)
        return super().create(prepared_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'destination_address_id' in vals and 'destination_address' not in vals:
            address = self.env['sale.destination.address'].browse(
                vals.get('destination_address_id')
            ).exists()
            vals['destination_address'] = address.name if address else False
        return super().write(vals)

    @api.onchange('destination_address_id')
    def _onchange_destination_address_id(self):
        for move in self:
            move.destination_address = move.destination_address_id.name or False
